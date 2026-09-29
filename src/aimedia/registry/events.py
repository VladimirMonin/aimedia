"""Безопасные диагностические события Registry (E03, C05b3).

События описаны в `docs/plans/README.md` (E03) и `docs/plans/logging-contract.md`:
`registry_loaded` несёт **число записей и детерминированный digest данных**,
`model_resolved` — выбранные ID, `model_validation_failed` — код и параметр.

Границы содержимого:

- `registry_loaded` не содержит путей файлов Registry: digest считается по
  фактическим данным, поэтому диагностика не раскрывает структуру диска;
- `model_resolved` несёт только canonical/requested model ID, provider ID и
  remote model ID — это публичные идентификаторы, а не секреты;
- `model_validation_failed` несёт только код доменной ошибки и имя параметра:
  prompt, содержимое изображений и пути источников в запись не попадают.

Логирование необязательно: чистые функции Registry работают и без логгера, а
диагностика включается на границе вызывающего слоя. Здесь нет Typer/Rich и нет
обращений к сети.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from aimedia.domain.errors import DomainError
from aimedia.logging import EventLogger
from aimedia.registry.models import EffectiveModelDefinition, ModelRecord

REGISTRY_LOADED_EVENT = "registry_loaded"
MODEL_RESOLVED_EVENT = "model_resolved"
MODEL_VALIDATION_FAILED_EVENT = "model_validation_failed"


def registry_digest(records: Iterable[ModelRecord]) -> str:
    """Детерминированный SHA-256 по данным Registry.

    Записи сериализуются в JSON-безопасном виде (enum → строка, дата → ISO,
    кортеж → массив) и сортируются по каноническому ID, поэтому digest не
    зависит от порядка файлов и не содержит путей. Изменение любой записи
    меняет digest, а одинаковые данные всегда дают одно значение.
    """
    canonical = sorted(
        (record.model_dump(mode="json") for record in records),
        key=lambda payload: str(payload.get("model_id")),
    )
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def log_registry_loaded(logger: EventLogger | None, records: Iterable[ModelRecord]) -> None:
    """Записать загрузку Registry: число записей и digest данных, без путей."""
    if logger is None:
        return
    ordered = tuple(records)
    logger.event(
        REGISTRY_LOADED_EVENT,
        details={"count": len(ordered), "digest": registry_digest(ordered)},
    )


def log_model_resolved(logger: EventLogger | None, effective: EffectiveModelDefinition) -> None:
    """Записать разрешение модели: выбранные ID, без секретов и содержимого входа."""
    if logger is None:
        return
    logger.event(
        MODEL_RESOLVED_EVENT,
        provider=effective.provider_id,
        details={
            "requested_model": effective.requested_model,
            "model_id": effective.model_id,
            "provider_id": effective.provider_id,
            "remote_model_id": effective.remote_model_id,
        },
    )


def log_model_validation_failed(logger: EventLogger | None, exc: DomainError) -> None:
    """Записать отказ validation: только код ошибки и имя параметра.

    Prompt, изображения и пути в details не попадают — запись остаётся
    безопасной для общего диагностического канала.
    """
    if logger is None:
        return
    details: dict[str, object] = {"code": exc.code.value}
    parameter = exc.details.get("parameter")
    if isinstance(parameter, str):
        details["parameter"] = parameter
    logger.event(MODEL_VALIDATION_FAILED_EVENT, level="WARNING", details=details)


__all__ = [
    "MODEL_RESOLVED_EVENT",
    "MODEL_VALIDATION_FAILED_EVENT",
    "REGISTRY_LOADED_EVENT",
    "log_model_resolved",
    "log_model_validation_failed",
    "log_registry_loaded",
    "registry_digest",
]
