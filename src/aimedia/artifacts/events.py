"""Безопасные диагностические события artifacts (E05, C08c2a).

События описаны в `docs/plans/README.md` (E05) и `docs/plans/logging-contract.md`:
`artifact_download_started`, `artifact_download_failed`, `artifact_converted`,
`artifact_saved`, `artifact_cleanup_failed`.

Границы содержимого (то же правило, что у событий storage и Registry):

- в запись попадают только локально проверенные значения: положительный числовой
  `job_id`, канонические enum `role`/`format` и числовые размеры/габариты;
- не попадают: сырой remote URL и его query-токены, абсолютные и относительные пути
  файлов, имена файлов (basename), текст prompt, ответ provider, текст исключения и
  его трассировка;
- корреляция child/context не наследуется: произвольные `command`, `provider`,
  `remote_operation`, `remote_job_id` не проходят в artifacts events.

`artifact_download_started`/`artifact_download_failed` — публичные helpers для
будущего provider-adapter (E06): файловый adapter получает уже доставленные байты и
эти события не пишет. `artifact_saved` означает, что опубликованный файл уже
существует. `artifact_converted` пишется только при фактической перекодировке.
`artifact_cleanup_failed` — только когда публикация сообщила о неудалённом временном
файле.

Логирование необязательно: при `logger=None` функции молча ничего не делают. Сами
helpers не глотают отказы логгера: best-effort-политику (не превращать успешную
публикацию в ошибку из-за сбоя диагностики) задаёт вызывающий слой
(`PillowArtifactStorage`).
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.artifacts import ArtifactRole
from aimedia.domain.requests import FinalFormat
from aimedia.logging import EventLogger

ARTIFACT_DOWNLOAD_STARTED_EVENT = "artifact_download_started"
ARTIFACT_DOWNLOAD_FAILED_EVENT = "artifact_download_failed"
ARTIFACT_CONVERTED_EVENT = "artifact_converted"
ARTIFACT_SAVED_EVENT = "artifact_saved"
ARTIFACT_CLEANUP_FAILED_EVENT = "artifact_cleanup_failed"

# Ни одно поле корреляции child/context не доказано безопасным для artifacts.
# `job_id` не в списке: helpers всегда передают его сами как проверенное значение.
_ARTIFACT_CORRELATION_FIELDS = ("command", "provider", "remote_operation", "remote_job_id")


def _canonical_enum_value[EventEnum: StrEnum](
    value: EventEnum | str, enum_type: type[EventEnum]
) -> str:
    """Пропускать в событие только enum или его точное каноническое значение."""
    if isinstance(value, enum_type):
        return value.value
    if type(value) is str and value in (member.value for member in enum_type):
        return value
    # Enum(value) включил бы недоверенную строку в текст ValueError.
    raise ValueError("Invalid artifact event enum value")


def _positive_job_id(value: int) -> str:
    """Только локально проверенный положительный числовой job ID."""
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid artifact event job ID")
    return str(value)


def _non_negative_count(value: int) -> int:
    """Только неотрицательное целое: bool и float в счётчик не проходят."""
    if type(value) is not int or value < 0:
        raise ValueError("Invalid artifact event count")
    return value


def log_artifact_download_started(
    logger: EventLogger | None,
    *,
    job_id: int,
    role: ArtifactRole | str,
) -> None:
    """Записать начало получения удалённого результата: роль и локальный ID.

    Helper для будущего provider-adapter (E06): файловый adapter уже получает
    готовые байты и событие не пишет. Raw URL и его query-токены не логируются.
    """
    if logger is None:
        return
    safe_job_id = _positive_job_id(job_id)
    safe_role = _canonical_enum_value(role, ArtifactRole)
    logger.event(
        ARTIFACT_DOWNLOAD_STARTED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_ARTIFACT_CORRELATION_FIELDS,
        details={"role": safe_role},
    )


def log_artifact_download_failed(
    logger: EventLogger | None,
    *,
    job_id: int,
    role: ArtifactRole | str,
) -> None:
    """Записать отказ получения: роль и ID, без URL и текста исключения.

    Причина отказа остаётся в цепочке исключения вызывающего слоя; событие её не
    повторяет, чтобы не переносить произвольный текст provider в диагностику.
    """
    if logger is None:
        return
    safe_job_id = _positive_job_id(job_id)
    safe_role = _canonical_enum_value(role, ArtifactRole)
    logger.event(
        ARTIFACT_DOWNLOAD_FAILED_EVENT,
        level="ERROR",
        job_id=safe_job_id,
        omit_correlation_fields=_ARTIFACT_CORRELATION_FIELDS,
        details={"role": safe_role},
    )


def log_artifact_converted(
    logger: EventLogger | None,
    *,
    job_id: int,
    role: ArtifactRole | str,
    source_format: FinalFormat | str,
    final_format: FinalFormat | str,
    width: int,
    height: int,
) -> None:
    """Записать фактическую перекодировку: форматы и размеры конечных байтов.

    Вызывается только когда конвертация действительно произошла: сохранение уже
    валидных байтов в том же формате события не порождает.
    """
    if logger is None:
        return
    safe_job_id = _positive_job_id(job_id)
    safe_role = _canonical_enum_value(role, ArtifactRole)
    safe_source = _canonical_enum_value(source_format, FinalFormat)
    safe_final = _canonical_enum_value(final_format, FinalFormat)
    safe_width = _non_negative_count(width)
    safe_height = _non_negative_count(height)
    logger.event(
        ARTIFACT_CONVERTED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_ARTIFACT_CORRELATION_FIELDS,
        details={
            "role": safe_role,
            "source_format": safe_source,
            "final_format": safe_final,
            "width": safe_width,
            "height": safe_height,
        },
    )


def log_artifact_saved(
    logger: EventLogger | None,
    *,
    job_id: int,
    role: ArtifactRole | str,
    final_format: FinalFormat | str,
    size_bytes: int,
    width: int,
    height: int,
) -> None:
    """Записать публикацию: роль, формат и фактические размеры файла.

    Событие пишется только после того, как опубликованный файл существует. Путь,
    имя файла и его принадлежность (managed/user_output) в запись не попадают.
    """
    if logger is None:
        return
    safe_job_id = _positive_job_id(job_id)
    safe_role = _canonical_enum_value(role, ArtifactRole)
    safe_final = _canonical_enum_value(final_format, FinalFormat)
    safe_size = _non_negative_count(size_bytes)
    safe_width = _non_negative_count(width)
    safe_height = _non_negative_count(height)
    logger.event(
        ARTIFACT_SAVED_EVENT,
        job_id=safe_job_id,
        omit_correlation_fields=_ARTIFACT_CORRELATION_FIELDS,
        details={
            "role": safe_role,
            "final_format": safe_final,
            "size_bytes": safe_size,
            "width": safe_width,
            "height": safe_height,
        },
    )


def log_artifact_cleanup_failed(
    logger: EventLogger | None,
    *,
    job_id: int,
    role: ArtifactRole | str,
) -> None:
    """Записать неудалённый временный файл: роль и ID, без пути.

    Вызывается только если публикация действительно сообщила сигнал
    `cleanup_failed_temp_path`. Сырой временный путь в событие не переносится.
    """
    if logger is None:
        return
    safe_job_id = _positive_job_id(job_id)
    safe_role = _canonical_enum_value(role, ArtifactRole)
    logger.event(
        ARTIFACT_CLEANUP_FAILED_EVENT,
        level="WARNING",
        job_id=safe_job_id,
        omit_correlation_fields=_ARTIFACT_CORRELATION_FIELDS,
        details={"role": safe_role},
    )


__all__ = [
    "ARTIFACT_CLEANUP_FAILED_EVENT",
    "ARTIFACT_CONVERTED_EVENT",
    "ARTIFACT_DOWNLOAD_FAILED_EVENT",
    "ARTIFACT_DOWNLOAD_STARTED_EVENT",
    "ARTIFACT_SAVED_EVENT",
    "log_artifact_cleanup_failed",
    "log_artifact_converted",
    "log_artifact_download_failed",
    "log_artifact_download_started",
    "log_artifact_saved",
]
