"""Request validation против effective definition (E03, C05b, R02).

Проверяемые утверждения этапа E03 (`docs/plans/README.md`):
недопустимый resolution, seed или число reference images останавливают request
**до любого вызова submit** (`submit_count == 0`); provider override действительно
сужает допустимый набор; alias разрешается в canonical ID; неизвестное
ограничение не превращается в придуманное число. Все записи синтетические —
реальные API ID и лимиты не выдумываются.

Fake provider живёт в `tests/support` и не регистрируется в production registry:
он доказывает только отсутствие submit после отказа validation.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_provider import FAKE_PROVIDER_ID, FakeImageProvider

from aimedia.domain import (
    CompiledPrompt,
    DomainError,
    FinalFormat,
    ImageGenerationRequest,
    InputKind,
    InputRef,
    InvalidParameterValueError,
    ModelRef,
    ProviderRef,
    TooManyReferenceImagesError,
    UnknownModelError,
    UnknownProviderError,
    UnsupportedCapabilityError,
    UnsupportedInputFormatError,
    UnsupportedParameterError,
)
from aimedia.registry import (
    CapabilityNode,
    EffectiveModelDefinition,
    InputLimit,
    ModelResolver,
    ModelStatus,
    ParameterOverride,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    ValidatedModelRequest,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord

PROMPT = CompiledPrompt(text="synthetic prompt", source_count=1)


def _resolution_enum() -> ParameterSpec:
    return ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K", "4K"), default="2K")


def _seed_spec(minimum: int | None = 0, maximum: int | None = None) -> ParameterSpec:
    return ParameterSpec(type=ParameterType.INTEGER, min=minimum, max=maximum)


def _record(
    *,
    model_id: str = "synthetic-image",
    provider_id: str = FAKE_PROVIDER_ID,
    aliases: tuple[str, ...] = (),
    parameters: dict[str, ParameterSpec] | None = None,
    capabilities: dict[str, bool | CapabilityNode] | None = None,
    inputs: dict[str, InputLimit] | None = None,
    outputs: dict[str, InputLimit] | None = None,
    status: ModelStatus = ModelStatus.ACTIVE,
    binding: ProviderBinding | None = None,
) -> ModelRecord:
    return ModelRecord(
        schema_version=1,
        model_id=model_id,
        name=f"Synthetic {model_id}",
        family="image",
        status=status,
        aliases=aliases,
        capabilities=capabilities or {},
        inputs=inputs or {},
        outputs=outputs or {},
        parameters=parameters if parameters is not None else {"resolution": _resolution_enum()},
        providers={provider_id: binding or ProviderBinding(remote_model_id="synthetic/remote")},
    )


def _effective(
    *,
    parameters: dict[str, ParameterSpec] | None = None,
    capabilities: dict[str, bool | CapabilityNode] | None = None,
    inputs: dict[str, InputLimit] | None = None,
    outputs: dict[str, InputLimit] | None = None,
    status: ModelStatus = ModelStatus.ACTIVE,
    aliases: tuple[str, ...] = (),
    binding: ProviderBinding | None = None,
) -> EffectiveModelDefinition:
    record = _record(
        parameters=parameters,
        capabilities=capabilities,
        inputs=inputs,
        outputs=outputs,
        status=status,
        aliases=aliases,
        binding=binding,
    )
    return ModelResolver([record]).resolve("synthetic-image", FAKE_PROVIDER_ID)


def _image_refs(count: int) -> list[InputRef]:
    """Синтетические reference images в порядке позиций (без чтения файлов)."""
    return [
        InputRef(kind=InputKind.IMAGE, path=f"ref-{position}.png", position=position)
        for position in range(count)
    ]


def _request(
    *,
    model_id: str = "synthetic-image",
    provider_id: str = FAKE_PROVIDER_ID,
    resolution: str | None = None,
    seed: int | None = None,
    images: list | None = None,
    max_images: int = 1,
    aspect_ratio: str | None = None,
) -> ImageGenerationRequest:
    return ImageGenerationRequest(
        provider=ProviderRef(id=provider_id),
        model=ModelRef(id=model_id),
        prompt=PROMPT,
        images=images or [],
        resolution=resolution,
        seed=seed,
        max_images=max_images,
        aspect_ratio=aspect_ratio,
    )


def _submit_count_after_validation(
    effective: EffectiveModelDefinition, request: ImageGenerationRequest
) -> tuple[int, DomainError | None]:
    """Провести validation и submit в реальной последовательности.

    Возвращает `submit_count` fake provider и ошибку validation (если она была):
    после отказа validation submit обязан остаться не вызванным.
    """
    provider = FakeImageProvider()
    try:
        validate_model_request(effective, request)
    except DomainError as exc:
        return provider.submit_count, exc
    asyncio.run(provider.submit(request))
    return provider.submit_count, None


def test_valid_request_returns_effective_for_adapter() -> None:
    """Успешная проверка несёт ту же effective definition для adapter."""
    effective = _effective(parameters={"resolution": _resolution_enum(), "seed": _seed_spec()})
    request = _request(resolution="2K", seed=42)

    validated = validate_model_request(effective, request)

    assert isinstance(validated, ValidatedModelRequest)
    assert validated.request is request
    assert validated.model_id == "synthetic-image"
    assert validated.provider_id == FAKE_PROVIDER_ID
    assert validated.remote_model_id == "synthetic/remote"


def test_invalid_resolution_stops_before_submit() -> None:
    """Недопустимый resolution отклоняется до вызова submit (`submit_count == 0`)."""
    effective = _effective(parameters={"resolution": _resolution_enum()})
    request = _request(resolution="8K")

    submits, error = _submit_count_after_validation(effective, request)

    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.code == "INVALID_PARAMETER_VALUE"
    assert error.details == {
        "parameter": "resolution",
        "value": "8K",
        "allowed": ["1K", "2K", "4K"],
    }


def test_seed_below_min_stops_before_submit() -> None:
    """Seed меньше документированного min отклоняется до submit."""
    effective = _effective(parameters={"seed": _seed_spec(minimum=0, maximum=100)})

    submits, error = _submit_count_after_validation(effective, _request(seed=-1))

    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details["parameter"] == "seed"
    assert error.details["min"] == 0


def test_seed_above_max_stops_before_submit() -> None:
    """Seed больше документированного max отклоняется до submit."""
    effective = _effective(parameters={"seed": _seed_spec(minimum=0, maximum=100)})

    submits, error = _submit_count_after_validation(effective, _request(seed=101))

    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details["max"] == 100


def test_too_many_reference_images_stops_before_submit() -> None:
    """Число reference images выше capability max отклоняется до submit."""
    effective = _effective(
        capabilities={"reference_images": CapabilityNode(supported=True, min=0, max=2)}
    )

    submits, error = _submit_count_after_validation(effective, _request(images=_image_refs(3)))

    assert submits == 0
    assert isinstance(error, TooManyReferenceImagesError)
    assert error.code == "TOO_MANY_REFERENCE_IMAGES"
    assert error.details == {"requested": 3, "max_references": 2}


def test_documented_image_formats_reject_missing_mime_without_path_details() -> None:
    effective = _effective(inputs={"images": InputLimit(formats=("png",))})
    submits, error = _submit_count_after_validation(effective, _request(images=_image_refs(1)))
    assert submits == 0
    assert isinstance(error, UnsupportedInputFormatError)
    assert error.details == {
        "parameter": "--image",
        "mime_type": None,
        "allowed_formats": ["png"],
    }
    assert "ref-0.png" not in str(error)


def test_undocumented_prompt_and_image_formats_remain_unrestricted() -> None:
    effective = _effective(inputs={"prompt": InputLimit(), "images": InputLimit()})
    request = _request(images=_image_refs(1)).model_copy(
        update={"prompt": CompiledPrompt(text="x" * 1000, source_count=1)}
    )
    assert validate_model_request(effective, request).request is request


def test_inputs_images_max_is_enforced_even_when_capability_has_no_bound() -> None:
    effective = _effective(
        capabilities={"reference_images": True}, inputs={"images": InputLimit(max=1)}
    )
    submits, error = _submit_count_after_validation(effective, _request(images=_image_refs(2)))
    assert submits == 0
    assert isinstance(error, TooManyReferenceImagesError)
    assert error.details == {"requested": 2, "max_references": 1}


@pytest.mark.parametrize(
    "cap_min, cap_max, input_min, input_max",
    [(2, 3, 1, 2), (1, 2, 2, 3)],
)
def test_reference_images_use_stricter_capability_and_input_bounds(
    cap_min: int, cap_max: int, input_min: int, input_max: int
) -> None:
    effective = _effective(
        capabilities={"reference_images": CapabilityNode(supported=True, min=cap_min, max=cap_max)},
        inputs={"images": InputLimit(min=input_min, max=input_max)},
    )
    with pytest.raises(TooManyReferenceImagesError) as above:
        validate_model_request(effective, _request(images=_image_refs(3)))
    assert above.value.details["max_references"] == 2
    with pytest.raises(InvalidParameterValueError) as below:
        validate_model_request(effective, _request(images=_image_refs(1)))
    assert below.value.details["min_references"] == 2
    validated = validate_model_request(effective, _request(images=_image_refs(2)))
    assert validated.model_id == "synthetic-image"


def test_inputs_images_required_rejects_zero_references_before_submit() -> None:
    effective = _effective(inputs={"images": InputLimit(required=True)})
    submits, error = _submit_count_after_validation(effective, _request())
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": "--image", "requested": 0, "min_references": 1}


def test_inputs_prompt_unsupported_rejects_mandatory_prompt_before_submit() -> None:
    effective = _effective(inputs={"prompt": InputLimit(supported=False)})
    submits, error = _submit_count_after_validation(effective, _request())
    assert submits == 0
    assert isinstance(error, UnsupportedCapabilityError)
    assert error.details == {"parameter": "prompt"}
    assert PROMPT.text not in str(error)


def test_inputs_images_min_is_enforced_without_capability_bound() -> None:
    effective = _effective(inputs={"images": InputLimit(min=1)})
    with pytest.raises(InvalidParameterValueError) as excinfo:
        validate_model_request(effective, _request(images=[]))
    assert excinfo.value.details["min_references"] == 1


def test_reference_images_below_min_are_rejected() -> None:
    """Число reference images ниже capability min отклоняется."""
    effective = _effective(
        capabilities={"reference_images": CapabilityNode(supported=True, min=1, max=4)}
    )
    with pytest.raises(InvalidParameterValueError) as excinfo:
        validate_model_request(effective, _request(images=[]))
    assert excinfo.value.details["min_references"] == 1


def test_inputs_images_supported_false_rejects_refs_without_capability() -> None:
    effective = _effective(inputs={"images": InputLimit(supported=False)})
    submits, error = _submit_count_after_validation(effective, _request(images=_image_refs(1)))
    assert submits == 0
    assert isinstance(error, UnsupportedCapabilityError)
    assert error.details == {"parameter": "--image", "requested": 1}
    assert validate_model_request(effective, _request()).model_id == "synthetic-image"


def test_inputs_images_unknown_support_does_not_reject_refs() -> None:
    effective = _effective(inputs={"images": InputLimit(supported=None)})
    assert validate_model_request(effective, _request(images=_image_refs(2))).model_id == (
        "synthetic-image"
    )


def test_unsupported_reference_capability_rejects_any_image() -> None:
    """Модель без поддержки reference images отклоняет их передачу."""
    effective = _effective(capabilities={"reference_images": CapabilityNode(supported=False)})
    with pytest.raises(UnsupportedCapabilityError):
        validate_model_request(effective, _request(images=_image_refs(1)))


def test_boolean_reference_capability_false_rejects_images() -> None:
    """Плоский флаг `reference_images: false` также отклоняет передачу images."""
    effective = _effective(capabilities={"reference_images": False})
    with pytest.raises(UnsupportedCapabilityError):
        validate_model_request(effective, _request(images=_image_refs(1)))


def test_unsupported_reference_capability_allows_no_images() -> None:
    """Модель без reference images принимает запрос без них (минимум 0)."""
    effective = _effective(capabilities={"reference_images": CapabilityNode(supported=False)})
    validated = validate_model_request(effective, _request(images=_image_refs(0)))
    assert validated.model_id == "synthetic-image"


def test_boolean_reference_capability_true_does_not_invent_limit() -> None:
    """Плоский флаг `reference_images: true` без границы не ограничивает число."""
    effective = _effective(capabilities={"reference_images": True})
    validated = validate_model_request(effective, _request(images=_image_refs(4)))
    assert validated.model_id == "synthetic-image"


def test_seed_absent_from_effective_definition_is_unsupported() -> None:
    """Seed без записи в effective definition — UnsupportedParameter."""
    effective = _effective(parameters={"resolution": _resolution_enum()})
    with pytest.raises(UnsupportedParameterError) as excinfo:
        validate_model_request(effective, _request(seed=7))
    assert excinfo.value.details["parameter"] == "seed"


def test_unknown_reference_limit_does_not_invent_number() -> None:
    """Неизвестная capability без границы не ограничивает число reference images."""
    effective = _effective(capabilities={"reference_images": CapabilityNode(supported=True)})

    validated = validate_model_request(effective, _request(images=_image_refs(3)))
    assert validated.remote_model_id == "synthetic/remote"


def test_absent_capability_does_not_invent_limit() -> None:
    """Отсутствующая capability означает «неизвестно», а не ноль."""
    effective = _effective(capabilities={})
    validated = validate_model_request(effective, _request(images=_image_refs(2)))
    assert validated.provider_id == FAKE_PROVIDER_ID


@pytest.mark.parametrize("name", ["resolution", "aspect_ratio", "quality", "output_format", "seed"])
def test_required_representable_parameter_without_default_rejects_missing_value(name: str) -> None:
    parameter_type = ParameterType.INTEGER if name == "seed" else ParameterType.STRING
    effective = _effective(parameters={name: ParameterSpec(type=parameter_type, required=True)})
    submits, error = _submit_count_after_validation(effective, _request())
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": name, "required": True}


@pytest.mark.parametrize(
    ("name", "default"),
    [("resolution", "2K"), ("quality", "high"), ("seed", 4)],
)
def test_required_representable_parameter_with_documented_default_accepts_missing_value(
    name: str, default: str | int
) -> None:
    parameter_type = ParameterType.INTEGER if name == "seed" else ParameterType.STRING
    effective = _effective(
        parameters={name: ParameterSpec(type=parameter_type, required=True, default=default)}
    )
    request = _request()
    assert validate_model_request(effective, request).request is request


def test_required_unrepresentable_parameter_is_not_invented() -> None:
    effective = _effective(
        parameters={"voice": ParameterSpec(type=ParameterType.STRING, required=True)}
    )
    assert validate_model_request(effective, _request()).model_id == "synthetic-image"


def test_string_parameter_max_length_rejects_without_leaking_content() -> None:
    secret = "private-prompt-or-secret"
    effective = _effective(
        parameters={"quality": ParameterSpec(type=ParameterType.STRING, max_length=4)}
    )
    request = _request().model_copy(update={"quality": secret})
    submits, error = _submit_count_after_validation(effective, request)
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": "quality", "length": len(secret), "max_length": 4}
    assert secret not in str(error)
    assert (
        validate_model_request(effective, request.model_copy(update={"quality": "best"})).model_id
        == "synthetic-image"
    )


def test_unknown_limit_without_bound_stays_accepted() -> None:
    """Параметр без объявленных границ/набора не выдумывает ограничение."""
    effective = _effective(
        parameters={
            "resolution": ParameterSpec(type=ParameterType.STRING),
            "seed": ParameterSpec(type=ParameterType.INTEGER),
        }
    )
    validated = validate_model_request(effective, _request(resolution="anything", seed=10**12))
    assert validated.model_id == "synthetic-image"


def test_provider_override_narrows_resolution() -> None:
    """Provider override действительно сужает набор: 4K из базы становится невалидным."""
    binding = ProviderBinding(
        remote_model_id="synthetic/narrowed",
        parameter_overrides={"resolution": ParameterOverride(values=("1K", "2K"))},
    )
    effective = _effective(binding=binding, parameters={"resolution": _resolution_enum()})

    with pytest.raises(InvalidParameterValueError) as excinfo:
        validate_model_request(effective, _request(resolution="4K"))
    assert excinfo.value.details["allowed"] == ["1K", "2K"]

    validated = validate_model_request(effective, _request(resolution="1K"))
    assert validated.remote_model_id == "synthetic/narrowed"


def test_alias_resolves_to_canonical_and_is_accepted() -> None:
    """Alias в запросе принимается, model_id остаётся каноническим."""
    effective = _effective(aliases=("syn-img",))

    validated = validate_model_request(effective, _request(model_id="syn-img"))
    assert validated.model_id == "synthetic-image"
    assert validated.effective.requested_model == "synthetic-image"


@pytest.mark.parametrize(
    "status", [ModelStatus.ACTIVE, ModelStatus.EXPERIMENTAL, ModelStatus.DEPRECATED]
)
def test_non_disabled_statuses_remain_valid(status: ModelStatus) -> None:
    effective = _effective(status=status)
    assert validate_model_request(effective, _request()).model_id == "synthetic-image"


def test_disabled_model_stops_before_submit() -> None:
    effective = _effective(status=ModelStatus.DISABLED)
    submits, error = _submit_count_after_validation(effective, _request())
    assert submits == 0
    assert isinstance(error, UnsupportedCapabilityError)
    assert error.code == "UNSUPPORTED_CAPABILITY"
    assert error.details == {"model": "synthetic-image", "status": "disabled"}
    assert "отключена" in error.message


def test_provider_mismatch_is_rejected() -> None:
    """Другой provider запроса не подменяется effective definition."""
    effective = _effective()
    with pytest.raises(UnknownProviderError) as excinfo:
        validate_model_request(effective, _request(provider_id="other-provider"))
    assert excinfo.value.code == "UNKNOWN_PROVIDER"


def test_model_mismatch_is_rejected() -> None:
    """Другая модель запроса не подменяется effective definition."""
    effective = _effective()
    with pytest.raises(UnknownModelError) as excinfo:
        validate_model_request(effective, _request(model_id="some-other-model"))
    assert excinfo.value.code == "UNKNOWN_MODEL"


def test_parameter_absent_from_effective_definition_is_unsupported() -> None:
    """Известный доменный параметр без записи в модели — UnsupportedParameter."""
    effective = _effective(parameters={"resolution": _resolution_enum()})
    with pytest.raises(UnsupportedParameterError) as excinfo:
        validate_model_request(effective, _request(aspect_ratio="1:1"))
    assert excinfo.value.details["parameter"] == "aspect_ratio"


def test_non_numeric_seed_parameter_is_unsupported() -> None:
    """Seed, объявленный нечисловым, не принимается молча."""
    effective = _effective(parameters={"seed": ParameterSpec(type=ParameterType.STRING)})
    with pytest.raises(UnsupportedParameterError):
        validate_model_request(effective, _request(seed=7))


def test_max_images_checked_against_parameter_bound() -> None:
    """Число выходных изображений сверяется с границей параметра max_images."""
    effective = _effective(
        parameters={"max_images": ParameterSpec(type=ParameterType.INTEGER, min=1, max=4)}
    )
    with pytest.raises(InvalidParameterValueError) as excinfo:
        validate_model_request(effective, _request(max_images=6))
    assert excinfo.value.details == {"parameter": "max_images", "value": 6, "max": 4}


def test_outputs_images_unsupported_rejects_single_image_before_submit() -> None:
    effective = _effective(outputs={"images": InputLimit(supported=False)})
    submits, error = _submit_count_after_validation(effective, _request())
    assert submits == 0
    assert isinstance(error, UnsupportedCapabilityError)
    assert error.details == {"parameter": "max_images"}


def test_outputs_images_max_rejects_without_parameter_or_capability() -> None:
    effective = _effective(outputs={"images": InputLimit(max=1)})
    submits, error = _submit_count_after_validation(effective, _request(max_images=2))
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": "max_images", "value": 2, "max": 1}


def test_outputs_images_min_rejects_below_documented_bound() -> None:
    effective = _effective(outputs={"images": InputLimit(min=2)})
    submits, error = _submit_count_after_validation(effective, _request(max_images=1))
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": "max_images", "value": 1, "min": 2}
    assert validate_model_request(effective, _request(max_images=2)).model_id == "synthetic-image"


def test_outputs_images_unknown_bounds_do_not_invent_limits() -> None:
    effective = _effective(outputs={"images": InputLimit()})
    assert validate_model_request(effective, _request(max_images=3)).model_id == "synthetic-image"


def test_max_images_checked_against_multiple_outputs_capability() -> None:
    """Число выходных изображений сверяется с capability multiple_outputs."""
    effective = _effective(capabilities={"multiple_outputs": CapabilityNode(supported=True, max=4)})
    with pytest.raises(InvalidParameterValueError):
        validate_model_request(effective, _request(max_images=6))


def test_multiple_outputs_min_rejects_below_documented_bound_before_submit() -> None:
    effective = _effective(capabilities={"multiple_outputs": CapabilityNode(supported=True, min=2)})
    submits, error = _submit_count_after_validation(effective, _request(max_images=1))
    assert submits == 0
    assert isinstance(error, InvalidParameterValueError)
    assert error.details == {"parameter": "max_images", "value": 1, "min": 2}
    assert validate_model_request(effective, _request(max_images=2)).model_id == "synthetic-image"


def test_multiple_outputs_unsupported_rejects_more_than_one() -> None:
    """Модель без нескольких outputs отклоняет max_images > 1."""
    effective = _effective(capabilities={"multiple_outputs": CapabilityNode(supported=False)})
    with pytest.raises(UnsupportedCapabilityError):
        validate_model_request(effective, _request(max_images=2))


def test_boolean_multiple_outputs_false_rejects_more_than_one() -> None:
    """Плоский флаг `multiple_outputs: false` также отклоняет max_images > 1."""
    effective = _effective(capabilities={"multiple_outputs": False})
    with pytest.raises(UnsupportedCapabilityError):
        validate_model_request(effective, _request(max_images=2))


def test_boolean_multiple_outputs_true_does_not_invent_limit() -> None:
    """Плоский флаг `multiple_outputs: true` без границы не ограничивает число."""
    effective = _effective(capabilities={"multiple_outputs": True})
    validated = validate_model_request(effective, _request(max_images=3))
    assert validated.provider_id == FAKE_PROVIDER_ID


def test_final_format_is_not_validated_against_provider_enum() -> None:
    """Локальный final_format не сверяется с enum Registry (baseline D08)."""
    effective = _effective(
        parameters={
            "output_format": ParameterSpec(
                type=ParameterType.ENUM, values=("png", "jpeg"), default="png"
            )
        }
    )
    request = _request().model_copy(update={"final_format": FinalFormat.WEBP})

    validated = validate_model_request(effective, request)
    assert validated.request.final_format is FinalFormat.WEBP
