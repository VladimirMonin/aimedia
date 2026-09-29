"""Ошибки storage-слоя: жизненный цикл схемы и владение соединением.

Это не доменные ошибки: они описывают сбой локальной инфраструктуры (SQLite,
миграции), а не отказ пользовательского Job. Поэтому они не наследуют
`DomainError` и не превращаются автоматически в `JobError`.
"""

from __future__ import annotations


class StorageError(Exception):
    """Базовая ошибка storage-слоя."""


class DatabaseOwnershipError(StorageError):
    """Соединение используется не тем потоком, который его открыл.

    `check_same_thread=False` намеренно не включается: SQLite-соединение
    принадлежит одному потоку, а не «переезжает» между ними молча.
    """


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
