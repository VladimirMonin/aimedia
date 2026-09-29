"""Безопасные диагностические события storage (E04, C06c1/C07a).

События описаны в `docs/plans/README.md` (E04) и `docs/plans/logging-contract.md`:
`database_opened`, `migration_started/completed/failed`, `job_created`,
`job_state_changed`, `remote_ref_saved`, `usage_recorded`, `cost_recorded`.

Используются только поля записи из `aimedia.logging.EVENT_FIELDS`: локальный
числовой `job_id`, enum `remote_operation` и структурированные `details` с
версиями/статусами/счётчиками. Свободный текст в `details` не пишется.

Границы содержимого (то же правило, что у событий Registry):

- в запись попадают только ID, статусы, версии и счётчики: локальный `job_id`,
  `kind`, `status`, `schema_version`, `previous_version`/`current_version`,
  номера применённых миграций и тип удалённой операции;
- `usage_recorded` несёт только нормализованные целочисленные счётчики usage и
  число полей `raw`, но не сам сырой provider blob;
- `cost_recorded` несёт Decimal-строку без экспоненты и проверенный доменный код
  валюты: сырая строка истории не становится значением события;
- не попадают: текст prompt (`compiled_prompt`), пути (путь файла БД, prompt-файлов
  и artifacts), значения и текст SQL, полный текст исключения и его трассировка;
- имена миграций и provider ID могут содержать произвольный текст и не пишутся;
- корреляция child/context не наследуется: произвольные `command`, `job_id`,
  `provider`, `remote_operation`, `remote_job_id` не проходят в storage events.

Логирование необязательно: при `logger=None` функции молча ничего не делают, и
storage работает без диагностического канала. События успеха пишутся **после**
окончательного commit; `migration_started` — до транзакции, `migration_failed` —
после отката. Запись в поток не удерживает SQLite transaction и не удлиняет её.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import TypeAdapter, ValidationError

from aimedia.domain.base import CurrencyCode, ExactDecimal
from aimedia.domain.costs import Usage
from aimedia.domain.refs import RemoteOperation
from aimedia.domain.requests import JobKind
from aimedia.domain.state import JobStatus
from aimedia.logging import EventLogger

DATABASE_OPENED_EVENT = "database_opened"
MIGRATION_STARTED_EVENT = "migration_started"
MIGRATION_COMPLETED_EVENT = "migration_completed"
MIGRATION_FAILED_EVENT = "migration_failed"
JOB_CREATED_EVENT = "job_created"
JOB_STATE_CHANGED_EVENT = "job_state_changed"
REMOTE_REF_SAVED_EVENT = "remote_ref_saved"
USAGE_RECORDED_EVENT = "usage_recorded"
COST_RECORDED_EVENT = "cost_recorded"

# Ни одно поле корреляции child/context не доказано безопасным для storage.
_STORAGE_CORRELATION_FIELDS = (
    "command",
    "job_id",
    "provider",
    "remote_operation",
    "remote_job_id",
)
_JOB_CORRELATION_FIELDS = ("command", "provider", "remote_operation", "remote_job_id")
_REMOTE_REF_CORRELATION_FIELDS = ("command", "provider", "remote_job_id")

# Доменные аннотации денег переиспользуются как единственный источник правил:
# `ExactDecimal` отклоняет float/bool и нечисловые строки, `CurrencyCode` принимает
# только три латинские буквы и нормализует регистр.
_AMOUNT_ADAPTER: TypeAdapter[Decimal] = TypeAdapter(ExactDecimal)
_CURRENCY_ADAPTER: TypeAdapter[str] = TypeAdapter(CurrencyCode)

# Ключи `*_tokens` редактируются общим logger как потенциальный секрет.
# Безопасные имена input/output/total_count обозначают именно token counts;
# float-оценки (`output_units`, `duration_seconds`) в событие не идут.
_USAGE_COUNT_FIELDS = (
    ("input_tokens", "input_count"),
    ("output_tokens", "output_count"),
    ("total_tokens", "total_count"),
    ("characters", "characters"),
)


def _canonical_enum_value[EventEnum: StrEnum](
    value: EventEnum | str, enum_type: type[EventEnum]
) -> str:
    """Пропускать в событие только enum или его точное каноническое значение."""
    if isinstance(value, enum_type):
        return value.value
    if type(value) is str and value in (member.value for member in enum_type):
        return value
    # Enum(value) включил бы недоверенную строку в текст ValueError.
    raise ValueError("Invalid storage event enum value")


def _local_job_id(value: int) -> str:
    if type(value) is not int:
        raise ValueError("Invalid storage event job ID")
    return str(value)


def _amount_text(value: Decimal | str | int) -> str:
    """Decimal-строка без экспоненты; недоверенное значение не попадает в запись.

    Проверка делегируется доменной аннотации `ExactDecimal`. Причина отказа не
    выводится: `ValidationError` повторяет входное значение в своём сообщении, а
    событие не должно переносить его даже в трассировку.
    """
    try:
        amount = _AMOUNT_ADAPTER.validate_python(value)
    except ValidationError:
        raise ValueError("Invalid storage event cost amount") from None
    return format(amount, "f")


def _currency_code(value: str) -> str:
    """Проверенный доменный код валюты; сырая строка в отказ не повторяется."""
    try:
        return _CURRENCY_ADAPTER.validate_python(value)
    except ValidationError:
        raise ValueError("Invalid storage event currency code") from None


def log_database_opened(
    logger: EventLogger | None,
    *,
    schema_version: int,
    peewee_version: str | None = None,
    sqlite_version: str | None = None,
) -> None:
    """Записать открытие базы: версии схемы и движка, без пути к файлу БД.

    Событие пишется после успешного открытия и приведения схемы к актуальной
    версии, поэтому `schema_version` — версия готовой к работе базы, а не
    промежуточное состояние. Путь к файлу БД в запись не попадает.
    """
    if logger is None:
        return
    details: dict[str, object] = {"schema_version": schema_version}
    if peewee_version is not None:
        details["peewee_version"] = peewee_version
    if sqlite_version is not None:
        details["sqlite_version"] = sqlite_version
    logger.event(
        DATABASE_OPENED_EVENT,
        details=details,
        omit_correlation_fields=_STORAGE_CORRELATION_FIELDS,
    )


def log_migration_started(
    logger: EventLogger | None,
    *,
    previous_version: int,
    target_version: int,
    pending_migrations: int,
) -> None:
    """Записать начало прогона миграций: версии и число ожидающих шагов."""
    if logger is None:
        return
    logger.event(
        MIGRATION_STARTED_EVENT,
        omit_correlation_fields=_STORAGE_CORRELATION_FIELDS,
        details={
            "previous_version": previous_version,
            "target_version": target_version,
            "pending_migrations": pending_migrations,
        },
    )


def log_migration_completed(
    logger: EventLogger | None,
    *,
    previous_version: int,
    current_version: int,
    applied: Sequence[int],
) -> None:
    """Записать успешный прогон: версии до/после и номера применённых миграций."""
    if logger is None:
        return
    logger.event(
        MIGRATION_COMPLETED_EVENT,
        omit_correlation_fields=_STORAGE_CORRELATION_FIELDS,
        details={
            "previous_version": previous_version,
            "current_version": current_version,
            "applied": [int(version) for version in applied],
        },
    )


def log_migration_failed(
    logger: EventLogger | None,
    *,
    version: int,
) -> None:
    """Записать отказ миграции: только номер шага, без имени и текста ошибки.

    Событие не заменяет исключение и не содержит SQL или сообщения драйвера:
    причину вызывающий слой видит в цепочке `MigrationFailedError.__cause__`.
    """
    if logger is None:
        return
    logger.event(
        MIGRATION_FAILED_EVENT,
        level="ERROR",
        omit_correlation_fields=_STORAGE_CORRELATION_FIELDS,
        details={"version": version},
    )


def log_job_created(
    logger: EventLogger | None,
    *,
    job_id: int | None,
    kind: JobKind | str,
    status: JobStatus | str,
) -> None:
    """Записать создание строки истории: локальный ID, kind и начальный статус."""
    if logger is None:
        return
    safe_kind = _canonical_enum_value(kind, JobKind)
    safe_status = _canonical_enum_value(status, JobStatus)
    safe_job_id = None if job_id is None else _local_job_id(job_id)
    logger.event(
        JOB_CREATED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=(
            _JOB_CORRELATION_FIELDS if job_id is not None else _STORAGE_CORRELATION_FIELDS
        ),
        details={"kind": safe_kind, "status": safe_status},
    )


def log_job_state_changed(
    logger: EventLogger | None,
    *,
    job_id: int,
    previous_status: JobStatus | str,
    current_status: JobStatus | str,
) -> None:
    """Записать переход статуса: только ID и пары статусов, без деталей Job."""
    if logger is None:
        return
    safe_previous = _canonical_enum_value(previous_status, JobStatus)
    safe_current = _canonical_enum_value(current_status, JobStatus)
    safe_job_id = _local_job_id(job_id)
    logger.event(
        JOB_STATE_CHANGED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_JOB_CORRELATION_FIELDS,
        details={"previous_status": safe_previous, "current_status": safe_current},
    )


def log_remote_ref_saved(
    logger: EventLogger | None,
    *,
    job_id: int,
    operation: RemoteOperation | str | None,
) -> None:
    """Записать факт сохранения ссылки без provider ID и удалённого ID.

    Пара «ID + тип операции» хранится в истории (baseline D10); диагностическое
    событие содержит только канонический тип операции, а не произвольные строки.
    """
    if logger is None:
        return
    safe_operation = (
        None if operation is None else _canonical_enum_value(operation, RemoteOperation)
    )
    safe_job_id = _local_job_id(job_id)
    logger.event(
        REMOTE_REF_SAVED_EVENT,
        job_id=safe_job_id,
        remote_operation=safe_operation,
        omit_correlation_fields=(
            _REMOTE_REF_CORRELATION_FIELDS if operation is not None else _JOB_CORRELATION_FIELDS
        ),
    )


def log_usage_recorded(
    logger: EventLogger | None,
    *,
    job_id: int,
    usage: Usage,
) -> None:
    """Записать сохранение usage: счётчики и число полей raw, без сырого blob.

    Событие подтверждает, что снимок usage записан в единственную строку Job, и
    одновременно не переносит provider payload в диагностический канал: `raw`
    заменяется числом полей, а float-метрики не пишутся вовсе.
    """
    if logger is None:
        return
    if not isinstance(usage, Usage):
        raise ValueError("Invalid storage event usage")
    safe_job_id = _local_job_id(job_id)
    details: dict[str, object] = {}
    for name, event_name in _USAGE_COUNT_FIELDS:
        value = getattr(usage, name)
        if value is not None:
            # Frozen Pydantic models may still be copied without validation; never
            # pass an unverified payload into the diagnostics channel.
            if type(value) is not int:
                raise ValueError("Invalid storage event usage count")
            details[event_name] = value
    if type(usage.raw) is not dict:
        raise ValueError("Invalid storage event raw usage")
    details["raw_field_count"] = len(usage.raw)
    logger.event(
        USAGE_RECORDED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_JOB_CORRELATION_FIELDS,
        details=details,
    )


def log_cost_recorded(
    logger: EventLogger | None,
    *,
    job_id: int,
    amount: Decimal | str | int,
    currency: str,
) -> None:
    """Записать сохранение фактической стоимости: Decimal-строка и код валюты.

    Оба значения проходят доменную проверку (`Amount`/`CurrencyCode`), поэтому
    событие несёт нормализованные значения, а не сырые строки истории: валюта
    приводится к верхнему регистру, сумма — к форме без экспоненты.
    """
    if logger is None:
        return
    safe_amount = _amount_text(amount)
    safe_currency = _currency_code(currency)
    safe_job_id = _local_job_id(job_id)
    logger.event(
        COST_RECORDED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_JOB_CORRELATION_FIELDS,
        details={"amount": safe_amount, "currency": safe_currency},
    )


__all__ = [
    "COST_RECORDED_EVENT",
    "DATABASE_OPENED_EVENT",
    "JOB_CREATED_EVENT",
    "JOB_STATE_CHANGED_EVENT",
    "MIGRATION_COMPLETED_EVENT",
    "MIGRATION_FAILED_EVENT",
    "MIGRATION_STARTED_EVENT",
    "REMOTE_REF_SAVED_EVENT",
    "USAGE_RECORDED_EVENT",
    "log_cost_recorded",
    "log_database_opened",
    "log_job_created",
    "log_job_state_changed",
    "log_migration_completed",
    "log_migration_failed",
    "log_migration_started",
    "log_remote_ref_saved",
    "log_usage_recorded",
]
