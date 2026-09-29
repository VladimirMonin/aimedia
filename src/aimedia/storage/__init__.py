"""Storage-слой aimedia: SQLite через Peewee, схема и её единственная миграция.

Слой отделён от домена (`aimedia.domain` остаётся IO-free) и от application:
`tests/architecture/test_dependencies.py` проверяет, что ни домен, ни application
не тянут Peewee. В C06a реализованы модель схемы, один migration runner и владелец
соединения с pragmas; repositories и денежные агрегаты — C06b/C07. C06c1 добавляет
необязательные безопасные события `database_opened`, `migration_started/completed/
failed`, `job_created`, `job_state_changed`, `remote_ref_saved` (`storage/events.py`).
C07a добавляет к ним `usage_recorded` и `cost_recorded`, которые пишутся после
финального commit сохранения usage/cost.
"""

from __future__ import annotations

from aimedia.storage.costs import PeeweeCostReportRepository
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
    DatabaseBusyError,
    DatabaseOwnershipError,
    InvalidStoredCostReportError,
    InvalidStoredJobStatusError,
    MigrationDefinitionError,
    MigrationFailedError,
    NestedStorageTransactionError,
    SchemaTooNewError,
    StorageError,
    is_database_busy,
)
from aimedia.storage.events import (
    COST_RECORDED_EVENT,
    DATABASE_OPENED_EVENT,
    JOB_CREATED_EVENT,
    JOB_STATE_CHANGED_EVENT,
    MIGRATION_COMPLETED_EVENT,
    MIGRATION_FAILED_EVENT,
    MIGRATION_STARTED_EVENT,
    REMOTE_REF_SAVED_EVENT,
    USAGE_RECORDED_EVENT,
    log_cost_recorded,
    log_database_opened,
    log_job_created,
    log_job_state_changed,
    log_migration_completed,
    log_migration_failed,
    log_migration_started,
    log_remote_ref_saved,
    log_usage_recorded,
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
from aimedia.storage.repository import PeeweeJobRepository

__all__ = [
    "ALL_MODELS",
    "ArtifactRecord",
    "COST_RECORDED_EVENT",
    "DATABASE_OPENED_EVENT",
    "DatabaseBusyError",
    "DatabaseManager",
    "DatabaseOwnershipError",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_DATABASE_FILENAME",
    "EngineVersions",
    "InputRecord",
    "InvalidStoredCostReportError",
    "InvalidStoredJobStatusError",
    "JOB_CREATED_EVENT",
    "JOB_STATE_CHANGED_EVENT",
    "JobRecord",
    "LATEST_SCHEMA_VERSION",
    "MIGRATION_COMPLETED_EVENT",
    "MIGRATION_FAILED_EVENT",
    "MIGRATION_STARTED_EVENT",
    "MIGRATION_TABLE_NAME",
    "MIGRATIONS",
    "Migration",
    "MigrationDefinitionError",
    "MigrationFailedError",
    "MigrationOutcome",
    "NestedStorageTransactionError",
    "PromptSourceRecord",
    "PeeweeCostReportRepository",
    "PeeweeJobRepository",
    "REMOTE_REF_SAVED_EVENT",
    "SCHEMA_TABLES",
    "SchemaMigrationRecord",
    "SchemaTooNewError",
    "StorageError",
    "USAGE_RECORDED_EVENT",
    "applied_migrations",
    "apply_migrations",
    "current_schema_version",
    "default_pragmas",
    "is_database_busy",
    "log_cost_recorded",
    "log_database_opened",
    "log_job_created",
    "log_job_state_changed",
    "log_migration_completed",
    "log_migration_failed",
    "log_migration_started",
    "log_remote_ref_saved",
    "log_usage_recorded",
    "open_database",
    "parse_version",
    "validate_migrations",
    "verify_engine_versions",
]
