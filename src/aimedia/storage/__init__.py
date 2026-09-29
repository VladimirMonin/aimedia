"""Storage-слой aimedia: SQLite через Peewee, схема и её единственная миграция.

Слой отделён от домена (`aimedia.domain` остаётся IO-free) и от application:
`tests/architecture/test_dependencies.py` проверяет, что ни домен, ни application
не тянут Peewee. В C06a реализованы модель схемы, один migration runner и владелец
соединения с pragmas; repositories и денежные агрегаты — C06b/C07.
"""

from __future__ import annotations

from aimedia.storage.database import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_DATABASE_FILENAME,
    DatabaseManager,
    EngineVersions,
    default_pragmas,
    open_database,
    parse_version,
    verify_engine_versions,
)
from aimedia.storage.errors import (
    DatabaseOwnershipError,
    MigrationDefinitionError,
    MigrationFailedError,
    SchemaTooNewError,
    StorageError,
)
from aimedia.storage.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
    MigrationOutcome,
    applied_migrations,
    apply_migrations,
    current_schema_version,
    validate_migrations,
)
from aimedia.storage.models import (
    ALL_MODELS,
    MIGRATION_TABLE_NAME,
    SCHEMA_TABLES,
    ArtifactRecord,
    InputRecord,
    JobRecord,
    PromptSourceRecord,
    SchemaMigrationRecord,
)

__all__ = [
    "ALL_MODELS",
    "ArtifactRecord",
    "DatabaseManager",
    "DatabaseOwnershipError",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_DATABASE_FILENAME",
    "EngineVersions",
    "InputRecord",
    "JobRecord",
    "LATEST_SCHEMA_VERSION",
    "MIGRATION_TABLE_NAME",
    "MIGRATIONS",
    "Migration",
    "MigrationDefinitionError",
    "MigrationFailedError",
    "MigrationOutcome",
    "PromptSourceRecord",
    "SCHEMA_TABLES",
    "SchemaMigrationRecord",
    "SchemaTooNewError",
    "StorageError",
    "applied_migrations",
    "apply_migrations",
    "current_schema_version",
    "default_pragmas",
    "open_database",
    "parse_version",
    "validate_migrations",
    "verify_engine_versions",
]
