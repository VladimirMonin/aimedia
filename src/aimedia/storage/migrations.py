"""Единственный migration runner storage (E04, C06a).

Решение baseline D11: в v0.1 используется **ровно один** migration runner. Для
закреплённой версии Peewee 3.19 проверен `playhouse.migrate`; он предоставляет
отдельные операции (`add_column`, `rename_column`, DDL-помощники), но **не**
ведёт учёт применённых миграций и **не** управляет версией схемы. Поэтому здесь
реализован один небольшой последовательный runner над публичным API Peewee, а
`playhouse.migrate` в проект не подключается — двух механизмов одновременно нет.
`playhouse.migrations.Runner` проверен отдельно: в установленной версии 3.19
модуль `playhouse.migrations` отсутствует (есть только `pwiz`; модуль появился в
более новых выпусках), поэтому выбрать его для 3.19 нельзя.

Runner ведёт себя так:

- версия схемы — максимальная записанная в `schema_migrations`;
- применяются только миграции с `version > current`, строго по порядку;
- каждая миграция выполняется в **своей** транзакции, поэтому её частичные DDL
  изменения откатываются вместе, а версия не записывается как успешная;
- база с версией выше известной коду не понижается: поднимается
  `SchemaTooNewError`;
- нарушение последовательности или дубликат версии в наборе — `MigrationDefinitionError`
  до любого изменения БД.

Транзакции синхронны: `apply` — обычная функция без `await`, поэтому сетевой вызов
не может удерживать транзакцию SQLite.

Реализованная цепочка: **v1** — все таблицы данных первого релиза, **v2** (`CN-01`)
— одна таблица `managed_input_copies`. v1 DDL не переписывается: новая таблица
входит в цепочку отдельным шагом, а не в `SCHEMA_TABLES` версии 1.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from peewee import Database

from aimedia.logging import EventLogger
from aimedia.storage.errors import (
    MigrationDefinitionError,
    MigrationFailedError,
    NestedStorageTransactionError,
    SchemaTooNewError,
)
from aimedia.storage.events import (
    log_migration_completed,
    log_migration_failed,
    log_migration_started,
)
from aimedia.storage.models import (
    ALL_MODELS,
    SCHEMA_TABLES,
    ManagedInputCopyRecord,
    SchemaMigrationRecord,
)

# Миграция — синхронная функция над уже подключённой базой. Возвращать значение
# ей не нужно: результат фиксируется записью в `schema_migrations`.
MigrationCallable = Callable[[Database], None]


@dataclass(frozen=True)
class Migration:
    """Одна именованная миграция с последовательным номером."""

    version: int
    name: str
    apply: MigrationCallable


@dataclass(frozen=True)
class MigrationOutcome:
    """Результат прогона: от какой версии к какой и что реально применилось."""

    previous_version: int
    current_version: int
    applied: tuple[int, ...]


def _create_initial_schema(database: Database) -> None:
    """v1: все таблицы данных первого релиза."""
    database.create_tables(list(SCHEMA_TABLES))


def _create_managed_input_copies(database: Database) -> None:
    """v2: одна таблица связи «вход → managed-копия» (`CN-01`).

    Создаётся только новая таблица: v1 DDL не переписывается, существующие строки
    `inputs` не переклассифицируются и не backfill-ятся, чужие файлы не читаются.
    """
    database.create_tables([ManagedInputCopyRecord])


# Продуктовая цепочка. Новая версия схемы добавляется сюда следующим элементом с
# номером `LATEST_SCHEMA_VERSION + 1`; отдельный файл миграций не заводится, потому
# что runner один и порядок объявлен в одном месте.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="001_initial", apply=_create_initial_schema),
    Migration(
        version=2,
        name="002_managed_input_copies",
        apply=_create_managed_input_copies,
    ),
)

LATEST_SCHEMA_VERSION: int = MIGRATIONS[-1].version


def validate_migrations(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    """Проверить последовательность 1..N без пропусков, дубликатов и пустых имён."""
    ordered = tuple(migrations)
    if not ordered:
        raise MigrationDefinitionError("Набор миграций пуст: схеме неоткуда взяться")
    expected = 1
    seen: set[int] = set()
    for migration in ordered:
        if not migration.name:
            raise MigrationDefinitionError(f"У миграции {migration.version} нет имени")
        if migration.version in seen:
            raise MigrationDefinitionError(f"Версия миграции {migration.version} повторяется")
        if migration.version != expected:
            raise MigrationDefinitionError(
                f"Нарушен порядок миграций: ожидалась версия {expected}, "
                f"получена {migration.version}"
            )
        seen.add(migration.version)
        expected += 1
    return ordered


def _ensure_migration_table(database: Database) -> None:
    """Создать `schema_migrations`, не трогая остальную схему."""
    with database.bind_ctx([SchemaMigrationRecord]):
        database.create_tables([SchemaMigrationRecord], safe=True)


def _migration_rows(database: Database) -> Iterator[SchemaMigrationRecord]:
    with database.bind_ctx([SchemaMigrationRecord]):
        yield from SchemaMigrationRecord.select().order_by(SchemaMigrationRecord.version)


def applied_migrations(database: Database) -> dict[int, str]:
    """Записанные версии схемы: `version → name`."""
    return {row.version: row.name for row in _migration_rows(database)}


def current_schema_version(database: Database) -> int:
    """Максимальная записанная версия схемы; 0 — версии ещё нет."""
    versions = applied_migrations(database)
    return max(versions, default=0)


def _utc_now_text() -> str:
    """Timestamp в той же форме, что у сериализации домена: ISO-8601 UTC с `Z`."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def apply_migrations(
    database: Database,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    logger: EventLogger | None = None,
) -> MigrationOutcome:
    """Довести схему до последней известной версии и вернуть результат.

    Функция синхронна и не открывает соединение сама: база передаётся уже
    подключённой, pragmas (`foreign_keys`, `journal_mode`, `busy_timeout`) к этому
    моменту выставлены владельцем соединения.

    Необязательный `logger` включает события `migration_started/completed/failed`
    (E04, C06c1). События пишутся вне транзакции миграции: `started` — до неё,
    `completed` — после commit, `failed` — после отката. Поэтому запись в поток не
    удерживает SQLite transaction и не удлиняет её. Прогон без ожидающих шагов
    событий не пишет: повторный запуск миграций остаётся no-op и не засоряет INFO.
    Внешняя транзакция запрещена до любых DDL и событий: внутренние commit
    миграций иначе могли бы быть отменены внешним rollback.
    """
    if database.transaction_depth() > 0:
        raise NestedStorageTransactionError("apply_migrations")
    ordered = validate_migrations(migrations)
    latest = ordered[-1].version

    _ensure_migration_table(database)
    recorded = applied_migrations(database)
    current = max(recorded, default=0)

    if current > latest:
        # Обратной миграции нет: новая схема должна распознаваться старой
        # программой как неподдерживаемая, а не понижаться автоматически.
        raise SchemaTooNewError(database_version=current, code_version=latest)

    expected_versions = list(range(1, current + 1))
    if sorted(recorded) != expected_versions:
        raise MigrationDefinitionError(
            f"История миграций повреждена: записаны версии {sorted(recorded)}, "
            f"ожидался непрерывный ряд {expected_versions}"
        )

    pending = [migration for migration in ordered if migration.version > current]
    if pending:
        log_migration_started(
            logger,
            previous_version=current,
            target_version=latest,
            pending_migrations=len(pending),
        )

    applied: list[int] = []
    for migration in pending:
        try:
            with database.atomic(), database.bind_ctx(ALL_MODELS):
                migration.apply(database)
                with database.bind_ctx([SchemaMigrationRecord]):
                    SchemaMigrationRecord.insert(
                        version=migration.version,
                        name=migration.name,
                        applied_at=_utc_now_text(),
                    ).execute()
        except Exception as exc:
            # Ошибка не записывается как успех: транзакция откатила и DDL, и запись
            # версии. Исходная причина сохраняется в цепочке исключений, а событие
            # отказа несёт только номер шага — без имени, текста ошибки и SQL.
            log_migration_failed(logger, version=migration.version)
            raise MigrationFailedError(version=migration.version, name=migration.name) from exc
        applied.append(migration.version)

    outcome = MigrationOutcome(
        previous_version=current,
        current_version=latest,
        applied=tuple(applied),
    )
    if pending:
        log_migration_completed(
            logger,
            previous_version=outcome.previous_version,
            current_version=outcome.current_version,
            applied=outcome.applied,
        )
    return outcome
