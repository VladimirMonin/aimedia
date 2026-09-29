"""View/help/JSON и effective definition используют один объект (E03, C05b3).

Проверяемые утверждения этапа E03 (`docs/plans/README.md`):
`tests/contracts/test_model_views.py` — справка/JSON отражают ту же effective
definition, которую использует validation; provider override меняет согласованно
и проверку, и рендер; неизвестное ограничение остаётся null/unknown; локальный
WebP не объявляется native-возможностью модели.

Все записи синтетические: реальные API ID и лимиты не выдумываются.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from aimedia.domain import (
    CompiledPrompt,
    FinalFormat,
    ImageGenerationRequest,
    InvalidParameterValueError,
    Job,
    JobKind,
    ModelRef,
    ProviderRef,
)
from aimedia.registry import (
    CapabilityNode,
    ModelResolver,
    ModelStatus,
    ParameterOverride,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    UnknownModelError,
    build_model_list,
    build_model_view,
    render_model_help,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord
from aimedia.registry.views import ProvenanceStatus

PROMPT = CompiledPrompt(text="synthetic prompt", source_count=1)
CREATED_AT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def _resolution_enum() -> ParameterSpec:
    return ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K", "4K"), default="2K")


def _record(
    *,
    model_id: str = "synthetic-image",
    aliases: tuple[str, ...] = (),
    capabilities: dict[str, bool | CapabilityNode] | None = None,
    parameters: dict[str, ParameterSpec] | None = None,
    binding: ProviderBinding | None = None,
    verification_source: str | None = None,
    checked_at: date | None = None,
) -> ModelRecord:
    from aimedia.registry.models import Verification

    verification = None
    if verification_source is not None:
        verification = Verification(
            checked_at=checked_at or date(2026, 1, 1), source=verification_source
        )
    return ModelRecord(
        schema_version=1,
        model_id=model_id,
        name=f"Synthetic {model_id}",
        family="image",
        status=ModelStatus.ACTIVE,
        aliases=aliases,
        capabilities=capabilities or {},
        parameters=parameters if parameters is not None else {"resolution": _resolution_enum()},
        providers={"polza": binding or ProviderBinding(remote_model_id="synthetic/remote")},
        verification=verification,
    )


def _effective(record: ModelRecord):
    return ModelResolver([record]).resolve(record.model_id, "polza")


def _request(*, resolution: str | None = None, final_format: FinalFormat | None = None):
    return ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id="synthetic-image"),
        prompt=PROMPT,
        resolution=resolution,
        final_format=final_format,
    )


def test_provider_override_changes_both_validation_and_json() -> None:
    """Один override сужает и реально проверяемый набор, и отрендеренный JSON.

    Это доказывает, что help/JSON не имеют второй таблицы допустимых значений:
    `4K` исчезает из view и одновременно становится невалидным для validation.
    """
    binding = ProviderBinding(
        remote_model_id="synthetic/narrowed",
        parameter_overrides={"resolution": ParameterOverride(values=("1K", "2K"))},
    )
    effective = _effective(_record(binding=binding))
    view = build_model_view(effective)

    assert view.parameters["resolution"].values == ("1K", "2K")
    assert view.remote_model_id == "synthetic/narrowed"

    with pytest.raises(InvalidParameterValueError) as excinfo:
        validate_model_request(effective, _request(resolution="4K"))
    assert excinfo.value.details["allowed"] == ["1K", "2K"]
    assert list(excinfo.value.details["allowed"]) == list(view.parameters["resolution"].values)

    validated = validate_model_request(effective, _request(resolution="1K"))
    assert validated.model_id == "synthetic-image"


def test_help_and_json_render_same_narrowed_values() -> None:
    """Human-help и JSON выводятся из одной view и не расходятся по значениям."""
    binding = ProviderBinding(
        remote_model_id="synthetic/narrowed",
        parameter_overrides={"resolution": ParameterOverride(values=("1K",), default="1K")},
    )
    effective = _effective(_record(binding=binding))
    view = build_model_view(effective)
    help_text = render_model_help(view)

    for value in view.parameters["resolution"].values or ():
        assert value in help_text
    assert "4K" not in help_text
    assert "4K" not in view.model_dump_json()


def test_view_is_pure_json_without_internal_objects() -> None:
    """JSON view содержит только скаляры: enum → строка, кортеж → массив, date → ISO."""
    effective = _effective(
        _record(
            capabilities={"reference_images": CapabilityNode(supported=True, max=4)},
            verification_source="provider_docs",
            checked_at=date(2026, 9, 28),
        )
    )
    view = build_model_view(effective)
    payload = json.loads(view.model_dump_json())

    assert payload["family"] == "image"
    assert payload["status"] == "active"
    assert payload["parameters"]["resolution"]["values"] == ["1K", "2K", "4K"]
    assert payload["capabilities"]["reference_images"]["max"] == 4
    assert payload["provenance"]["checked_at"] == "2026-09-28"
    assert "Path" not in view.model_dump_json()


def test_unknown_limits_stay_null_in_json_and_unknown_in_help() -> None:
    """Неизвестная граница остаётся null/«not documented», а не придуманным числом."""
    effective = _effective(
        _record(
            capabilities={"reference_images": CapabilityNode(supported=True)},
            parameters={"seed": ParameterSpec(type=ParameterType.INTEGER)},
        )
    )
    view = build_model_view(effective)
    payload = json.loads(view.model_dump_json())

    assert payload["capabilities"]["reference_images"]["max"] is None
    assert payload["parameters"]["seed"]["min"] is None
    assert payload["parameters"]["seed"]["max"] is None
    help_text = render_model_help(view)
    assert "max not documented" in help_text or "no documented bound" in help_text


def test_local_webp_processing_not_advertised_as_native_provider_format() -> None:
    """Локальный WebP не появляется как native-возможность модели.

    Модель документирует только provider output `png`/`jpeg`; `--format webp` —
    локальный `final_format` (baseline D08). View не должен показывать `webp` как
    поддержанный моделью, но локальный запрос остаётся валидным.
    """
    effective = _effective(
        _record(
            parameters={
                "output_format": ParameterSpec(
                    type=ParameterType.ENUM, values=("png", "jpeg"), default="png"
                )
            }
        )
    )
    view = build_model_view(effective)
    payload = json.loads(view.model_dump_json())

    assert "webp" not in json.dumps(payload)
    assert "webp" not in render_model_help(view)
    assert payload["parameters"]["output_format"]["values"] == ["png", "jpeg"]

    # Локальная конвертация WebP не проверяется против provider output.
    validated = validate_model_request(
        effective, _request(final_format=FinalFormat.WEBP, resolution=None)
    )
    assert validated.request.final_format is FinalFormat.WEBP


def test_provenance_distinguishes_documented_from_unverified() -> None:
    """`documented` (источник + дата) отличается от `unverified` без провенанса."""
    documented = build_model_view(
        _effective(_record(verification_source="provider_docs", checked_at=date(2026, 9, 28)))
    )
    unverified = build_model_view(_effective(_record()))

    assert documented.provenance.status is ProvenanceStatus.DOCUMENTED
    assert documented.provenance.source == "provider_docs"
    assert documented.provenance.checked_at == date(2026, 9, 28)
    assert unverified.provenance.status is ProvenanceStatus.UNVERIFIED
    assert unverified.provenance.source is None


def test_model_list_is_derived_from_records_without_second_table() -> None:
    """`models list` строится из загруженных записей, без hardcoded списка."""
    summaries = build_model_list(
        [
            _record(model_id="synthetic-b", aliases=("syn-b",)),
            _record(model_id="synthetic-a"),
        ]
    )
    assert [summary.id for summary in summaries] == ["synthetic-a", "synthetic-b"]
    assert summaries[1].aliases == ("syn-b",)
    assert summaries[0].providers == ("polza",)


def test_saved_job_survives_removed_registry_record_without_resolver() -> None:
    """Удалённая из Registry модель не ломает чтение сохранённого Job.

    История хранит snapshot `model_id`/`remote_model_id`/provider, поэтому Job
    читается из сохранённого JSON без обращения к Registry. Resolver над пустым
    каталогом (запись удалена) — для сравнения — не может разрешить модель, но
    сериализация Job от него не зависит (`06-model-registry.md`, «Историческая
    воспроизводимость»).
    """
    effective = _effective(_record(model_id="synthetic-removed"))
    request = ImageGenerationRequest(
        provider=ProviderRef(id=effective.provider_id),
        model=ModelRef(id=effective.model_id),
        prompt=PROMPT,
    )
    saved = Job(
        id=7,
        kind=JobKind.IMAGE_GENERATE,
        provider=ProviderRef(id=effective.provider_id),
        model=ModelRef(id=effective.model_id),
        remote_model_id=effective.remote_model_id,
        request=request,
        created_at=CREATED_AT,
    )

    # Пустой Registry после удаления записи: resolver модель не знает.
    with pytest.raises(UnknownModelError):
        ModelResolver([]).resolve("synthetic-removed", "polza")

    # Сохранённый Job читается из snapshot и сохраняет remote_model_id.
    restored = Job.model_validate_json(saved.model_dump_json())
    assert restored.model.id == "synthetic-removed"
    assert restored.remote_model_id == "synthetic/remote"
    assert restored.request.model.id == "synthetic-removed"
