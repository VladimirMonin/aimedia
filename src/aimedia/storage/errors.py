"""Ошибки storage-слоя: жизненный цикл схемы и владение соединением.

Это не доменные ошибки: они описывают сбой локальной инфраструктуры (SQLite,
миграции), а не отказ пользовательского Job. Поэтому они не наследуют
`DomainError` и не превращаются автоматически в `JobError`.
"""

from __future__ import annotations


class StorageError(Exception):
    """Базовая ошибка storage-слоя."""


class NestedStorageTransactionError(StorageError):
    """Мутация запрещена внутри внешней транзакции: события ждут финального commit."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} нельзя вызвать внутри внешней транзакции storage")
        self.operation = operation


class InvalidStoredJobStatusError(StorageError):
    """Сохранённый статус Job не принадлежит доменному набору состояний."""

    def __init__(self) -> None:
        super().__init__("Сохранённый статус Job некорректен")


class DatabaseOwnershipError(StorageError):
    """Соединение используется не тем потоком, который его открыл.

    `check_same_thread=False` намеренно не включается: SQLite-соединение
    принадлежит одному потоку, а не «переезжает» между ними молча.
    """


# Первичные коды SQLite, означающие ожидаемую блокировку: `SQLITE_BUSY` и
# `SQLITE_LOCKED`. Расширенные коды (`SQLITE_BUSY_SNAPSHOT`, `SQLITE_BUSY_TIMEOUT`,
# `SQLITE_LOCKED_SHAREDCACHE`) содержат первичный код в младшем байте.
SQLITE_BUSY_PRIMARY_CODES: tuple[int, ...] = (5, 6)

# Текст, которым драйвер сообщает о занятой базе, когда `sqlite_errorcode`
# недоступен (старые версии Python).
SQLITE_BUSY_MESSAGE = "database is locked"


def is_database_busy(exc: BaseException) -> bool:
    """Отличить ожидаемую блокировку SQLite от прочих ошибок драйвера.

    Peewee заворачивает `sqlite3.OperationalError` в собственный тип и теряет
    атрибуты расширенного кода, поэтому код ищется как в самом исключении, так и в
    его цепочке (`__context__`/`__cause__`). Проверка узкая: она отвечает только на
    вопрос «это busy/locked», а не подменяет разбор остальных ошибок SQLite.
    """
    candidate: BaseException | None = exc
    for _ in range(4):
        if candidate is None:
            break
        code = getattr(candidate, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in SQLITE_BUSY_PRIMARY_CODES:
            return True
        candidate = candidate.__context__ or candidate.__cause__
    return SQLITE_BUSY_MESSAGE in str(exc)


class DatabaseBusyError(StorageError):
    """SQLite не отдал запись за отведённый `busy_timeout`.

    Это локальный, управляемый отказ: транзакция откатывается целиком, история не
    остаётся полузаписанной, а вызывающий слой может повторить **локальную**
    запись. Ошибка не является отказом provider, поэтому она не наследует
    `DomainError` и не даёт повода повторно отправлять remote submit.
    """

    def __init__(self) -> None:
        super().__init__(
            "SQLite занят другим писателем: запись не уложилась в busy_timeout. "
            "Транзакция откачена целиком"
        )


class SchemaTooNewError(StorageError):
    """Схема БД новее, чем знает текущий код.

    Обратная миграция (downgrade) не выполняется: новая версия программы должна
    распознаваться старой как неподдерживаемая, а не понижаться автоматически.
    """

    def __init__(self, *, database_version: int, code_version: int) -> None:
        super().__init__(
            "Схема БД новее, чем поддерживает эта версия программы: "
            f"в базе версия {database_version}, код знает до {code_version}"
        )
        self.database_version = database_version
        self.code_version = code_version


class MigrationDefinitionError(StorageError):
    """Набор миграций некорректен (порядок, дубликаты, нумерация)."""


class MigrationFailedError(StorageError):
    """Миграция завершилась ошибкой и была откатана.

    Ошибка не записывается как успешная: версия миграции в `schema_migrations` не
    появляется, а транзакция откатывает её частичные изменения.
    """

    def __init__(self, *, version: int, name: str) -> None:
        super().__init__(f"Миграция {version} ({name}) завершилась ошибкой и откатана")
        self.version = version
        self.name = name
