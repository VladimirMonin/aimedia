"""Application-граница Registry логирует безопасные события (E03, C05b3).

События E03 (`docs/plans/README.md`): `registry_loaded` с числом записей и
детерминированным digest данных, `model_resolved`, `model_validation_failed`.
Проверяется не только наличие события, но и его границы: нет путей файлов, prompt,
содержимого изображений и секретов; digest детерминирован и меняется вместе с
данными.

Все записи синтетические: реальные API ID и лимиты не выдумываются.
"""

from __future__ import annotations

import io
import json

import pytest

from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    InvalidParameterValueError,
    ModelRef,
    ProviderRef,
)
from aimedia.logging import EventLogger
from aimedia.registry import (
    ModelResolver,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    log_model_resolved,
    log_model_validation_failed,
    log_registry_loaded,
    registry_digest,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord, ModelStatus

PROMPT = CompiledPrompt(text="SECRET-PROMPT-CANARY-3f9a", source_count=1)


def _logger() -> tuple[EventLogger, io.StringIO]:
    stream = io.StringIO()
    return EventLogger(stream=stream, min_level="DEBUG"), stream


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _record(model_id: str = "synthetic-image", **overrides: object) -> ModelRecord:
    payload: dict[str, object] = {
        "schema_version": 1,
        "model_id": model_id,
        "name": f"Synthetic {model_id}",
        "family": "image",
        "status": ModelStatus.ACTIVE,
        "parameters": {
            "resolution": ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K"), default="1K")
        },
        "providers": {"polza": ProviderBinding(remote_model_id="synthetic/remote")},
    }
    payload.update(overrides)
    return ModelRecord(**payload)  # type: ignore[arg-type]


def test_registry_loaded_logs_count_and_digest_without_paths() -> None:
    """`registry_loaded` несёт число записей и digest, но не путь файла."""
    logger, stream = _logger()

    log_registry_loaded(logger, [_record("synthetic-a"), _record("synthetic-b")])

    records = _records(stream)
    assert [record["event"] for record in records] == ["registry_loaded"]
    details = records[0]["details"]
    assert details["count"] == 2
    assert isinstance(details["digest"], str) and len(details["digest"]) == 64
    # В записи нет ни ID моделей, ни путей к файлам Registry — только число и digest.
    assert "synthetic" not in stream.getvalue()
    assert ".yaml" not in stream.getvalue()


def test_digest_is_deterministic_and_order_independent() -> None:
    """Одинаковые данные дают один digest независимо от порядка записей."""
    first = registry_digest([_record("synthetic-a"), _record("synthetic-b")])
    second = registry_digest([_record("synthetic-b"), _record("synthetic-a")])
    assert first == second


def test_digest_changes_when_data_changes() -> None:
    """Изменение записи меняет digest, а не остаётся прежним."""
    base = registry_digest([_record("synthetic-a")])
    changed = registry_digest([_record("synthetic-a", name="Renamed")])
    assert base != changed


def test_model_resolved_logs_ids_only() -> None:
    """`model_resolved` несёт выбранные ID, без prompt и секретов."""
    logger, stream = _logger()
    effective = ModelResolver([_record()]).resolve("synthetic-image", "polza")

    log_model_resolved(logger, effective)

    records = _records(stream)
    assert records[0]["event"] == "model_resolved"
    assert records[0]["provider"] == "polza"
    assert records[0]["details"] == {
        "requested_model": "synthetic-image",
        "model_id": "synthetic-image",
        "provider_id": "polza",
        "remote_model_id": "synthetic/remote",
    }
    assert "SECRET" not in stream.getvalue()


def test_model_validation_failed_logs_code_and_parameter_only() -> None:
    """Отказ validation логирует код и параметр, но не prompt и не его содержимое."""
    logger, stream = _logger()
    effective = ModelResolver([_record()]).resolve("synthetic-image", "polza")
    request = ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id="synthetic-image"),
        prompt=PROMPT,
        resolution="8K",
    )

    with pytest.raises(InvalidParameterValueError):
        validate_model_request(effective, request, logger=logger)

    records = _records(stream)
    assert [record["event"] for record in records] == ["model_validation_failed"]
    assert records[0]["level"] == "WARNING"
    assert records[0]["details"] == {
        "code": "INVALID_PARAMETER_VALUE",
        "parameter": "resolution",
    }
    assert PROMPT.text not in stream.getvalue()
    assert "8K" not in stream.getvalue()


def test_log_model_validation_failed_omits_non_parameter_details() -> None:
    """Прямой вызов не протаскивает лишние details из ошибки."""
    logger, stream = _logger()
    from aimedia.domain import TooManyReferenceImagesError

    log_model_validation_failed(
        logger,
        TooManyReferenceImagesError("too many", details={"requested": 3, "max_references": 2}),
    )

    assert _records(stream)[0]["details"] == {"code": "TOO_MANY_REFERENCE_IMAGES"}


def test_registry_logging_without_logger_is_silent() -> None:
    """Чистые функции Registry не обязаны логировать и не падают без логгера."""
    log_registry_loaded(None, [_record()])
    log_model_resolved(None, ModelResolver([_record()]).resolve("synthetic-image", "polza"))
