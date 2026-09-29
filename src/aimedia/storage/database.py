"""Владение соединением SQLite и его pragmas (`07-storage-history-costs.md`).

`DatabaseManager` отвечает за один локальный SQLite-файл: соединение, pragmas,
жизненный цикл и запуск единственного migration runner. Repositories получают базу
через `database`/`connection` и не открывают соединений сами.

Границы, которые нельзя ослаблять:

- `check_same_thread=False` **не** включается: соединение принадлежит потоку,
  который его открыл, а чужой поток получает `DatabaseOwnershipError`, а не молча
  второе соединение к тому же файлу;
- никакого thread offload «на всякий случай»: синхронная работа в одном потоке —
  выбранная модель, а `asyncio.to_thread` вокруг storage не добавляется;
- pragmas выставляются Peewee на **каждом** соединении до первого запроса:
  `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`;
- транзакции не удерживаются во время сетевого вызова: методы синхронны, а
  `await` в них отсутствует.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self, cast

import peewee
from peewee import SqliteDatabase

from aimedia.logging import EventLogger
from aimedia.storage.errors import DatabaseOwnershipError, StorageError
from aimedia.storage.events import log_database_opened
from aimedia.storage.migrations import MigrationOutcome, apply_migrations

# Закреплённый диапазон Peewee (`pyproject.toml`: `peewee>=3.19,<4`). Runner
# выбирался именно под 3.19, поэтому несовпадение — явный отказ, а не тихая работа
# на непроверенном API.
MIN_PEEWEE_VERSION = (3, 19)
MAX_PEEWEE_MAJOR_EXCLUSIVE = 4

# Имя файла базы в data-root (`07-storage-history-costs.md`: `Data: database.sqlite3`).
DEFAULT_DATABASE_FILENAME = "database.sqlite3"

# Разумный busy timeout для 3–5 параллельных Job (план: «включён reasonable busy
# timeout»). Значение в миллисекундах — так его принимает SQLite.
DEFAULT_BUSY_TIMEOUT_MS = 5000

# Сколько секунд sqlite3 ждёт блокировку на уровне драйвера до `SQLITE_BUSY`. Это
# отдельный от `busy_timeout` слой: первый ждёт блокировку файла, второй —
# завершение чужой транзакции внутри SQLite.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0


def default_pragmas(busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> tuple[tuple[str, str], ...]:
    """Pragmas соединения storage в порядке применения."""
    return (
        ("foreign_keys", "ON"),
        ("journal_mode", "WAL"),
        ("busy_timeout", str(busy_timeout_ms)),
    )


@dataclass(frozen=True)
class EngineVersions:
    """Фактические версии движка: Peewee (ORM) и SQLite (драйвер `sqlite3`)."""

    peewee: str
    sqlite: str


def parse_version(text: str) -> tuple[int, ...]:
    """Числовой префикс версии: `3.19.0` → `(3, 19, 0)`, `3.19.0rc1` → `(3, 19, 0)`."""
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def verify_engine_versions(
    *, peewee_version: str | None = None, sqlite_version: str | None = None
) -> EngineVersions:
    """Проверить, что движок входит в закреплённый диапазон, и вернуть версии.

    Несовпадение — `StorageError`: непроверенная версия ORM не должна молча
    выполнять миграции схемы пользовательской истории.
    """
    resolved_peewee = peewee.__version__ if peewee_version is None else peewee_version
    resolved_sqlite = sqlite3.sqlite_version if sqlite_version is None else sqlite_version

    parsed = parse_version(resolved_peewee)
    if len(parsed) < 2:
        raise StorageError(f"Не разобрана версия Peewee: {resolved_peewee!r}")
    if parsed < MIN_PEEWEE_VERSION or parsed[0] >= MAX_PEEWEE_MAJOR_EXCLUSIVE:
        raise StorageError(
            "Peewee вне закреплённого диапазона "
            f">={'.'.join(map(str, MIN_PEEWEE_VERSION))},<"
            f"{MAX_PEEWEE_MAJOR_EXCLUSIVE}: установлена {resolved_peewee}"
        )
    if len(parse_version(resolved_sqlite)) < 3:
        raise StorageError(f"Не разобрана версия SQLite: {resolved_sqlite!r}")

    return EngineVersions(peewee=resolved_peewee, sqlite=resolved_sqlite)


class DatabaseManager:
    """Владелец одного SQLite-соединения и его схемы.

    Менеджер не создаёт каталоги: путь задаёт вызывающий слой (composition root
    знает data-root, тесты — `tmp_path`).
    """

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._path = Path(path)
        self._owner_thread: int | None = None
        self.engine_versions: EngineVersions | None = None
        self._database = SqliteDatabase(
            str(self._path),
            pragmas=default_pragmas(busy_timeout_ms),
            timeout=connect_timeout_seconds,
        )

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._owner_thread is not None and not self._database.is_closed()

    def _ensure_owner(self) -> None:
        """Отклонить обращение из потока, который не открывал соединение."""
        owner = self._owner_thread
        if owner is not None and owner != threading.get_ident():
            raise DatabaseOwnershipError(
                "Соединение storage принадлежит потоку "
                f"{owner}, а обращение пришло из потока {threading.get_ident()}"
            )

    def connect(self) -> None:
        """Открыть соединение и применить pragmas.

        Pragmas применяет Peewee в `_add_conn_hooks` до первого запроса, поэтому
        `foreign_keys=ON` действует и на первую миграцию.
        """
        self._ensure_owner()
        if not self._database.is_closed():
            return
        parent = self._path.parent
        if not parent.exists():
            raise FileNotFoundError(
                f"Каталог базы не существует: {parent}. Storage не создаёт data-root сам"
            )
        # Версия ORM проверяется до первой миграции: непроверенный Peewee не должен
        # незаметно менять фактическое поведение DDL/транзакций. Присваивается
        # только после успешного `connect`, чтобы состояние не опережало факт.
        versions = verify_engine_versions()
        self._database.connect()
        self.engine_versions = versions
        self._owner_thread = threading.get_ident()

    def close(self) -> None:
        """Закрыть соединение. Чужой поток закрывать его не имеет права."""
        self._ensure_owner()
        if not self._database.is_closed():
            self._database.close()
        self._owner_thread = None
        self.engine_versions = None

    @property
    def database(self) -> SqliteDatabase:
        """База для запросов repositories; доступна только владельцу потока."""
        self._ensure_owner()
        return self._database

    @property
    def connection(self) -> sqlite3.Connection:
        """Фактическое соединение sqlite3 (диагностика и проверки pragmas)."""
        self._ensure_owner()
        return cast(sqlite3.Connection, self._database.connection())

    def migrate(self, *, logger: EventLogger | None = None) -> MigrationOutcome:
        """Довести схему до последней известной версии.

        Необязательный `logger` включает события `migration_started/completed/failed`
        (E04, C06c1); без него миграции работают молча.
        """
        self._ensure_owner()
        return apply_migrations(self._database, logger=logger)

    def open(self, *, logger: EventLogger | None = None) -> MigrationOutcome:
        """Открыть соединение и применить миграции; при ошибке закрыть его.

        После успешной миграции пишется `database_opened` с версией схемы и
        версиями движка. Путь к файлу БД в событие не попадает.
        """
        self.connect()
        try:
            outcome = self.migrate(logger=logger)
        except Exception:
            self.close()
            raise
        versions = self.engine_versions
        log_database_opened(
            logger,
            schema_version=outcome.current_version,
            peewee_version=None if versions is None else versions.peewee,
            sqlite_version=None if versions is None else versions.sqlite,
        )
        return outcome

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_database(
    path: Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    logger: EventLogger | None = None,
) -> DatabaseManager:
    """Открыть базу по пути и довести схему до актуальной версии.

    Единственная точка входа composition root: после неё repositories работают с
    уже подключённой и мигрированной базой. Необязательный `logger` передаётся
    дальше в миграции и включает `database_opened`.
    """
    manager = DatabaseManager(
        path,
        busy_timeout_ms=busy_timeout_ms,
        connect_timeout_seconds=connect_timeout_seconds,
    )
    manager.open(logger=logger)
    return manager
