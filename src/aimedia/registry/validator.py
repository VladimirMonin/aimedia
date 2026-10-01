"""Проверка запроса против Effective Model Definition (E03, C05b).

Validator — отдельная обязанность от resolver (`06-model-registry.md`,
«Validator»): он работает только с уже построенной effective definition и
типизированным доменным запросом, не читает YAML и не ходит в сеть. Порядок
проверок повторяет документированный flow: binding к provider, ненулевые
capability, enum-параметры, числовые границы, prompt и reference images.

Ключевой инвариант — отклоняются только **явно документированные** ограничения:
enum-набор и границы берутся из effective definition, а неизвестное ограничение
(`None`) не превращается в придуманное число. Известная возможность (`supported`)
без численной границы не ограничивает значение. Отсутствующий в effective
definition параметр — `UNSUPPORTED_PARAMETER` (раздел «Unknown parameter»), а не
молчаливое принятие.

Валидация не заменяет server-side validation provider: локальная проверка не
гарантирует, что удалённый API примет запрос.

Успешный результат несёт саму effective definition для последующего adapter:
именно она содержит `remote_model_id` и выбранный `provider_id`, а не доменный
запрос. Домен остаётся registry-independent: знание приходит из Registry, а не
наоборот.

В сообщениях и `details` нет prompt и содержимого изображений — только имя
параметра, статус модели, запрошенное значение, документированный набор/граница
и число reference images. Ограничение prompt представлено только длиной, а
формат reference image — только MIME, без путей.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aimedia.domain.errors import (
    DomainError,
    InvalidParameterValueError,
    TooManyReferenceImagesError,
    UnknownModelError,
    UnknownProviderError,
    UnsupportedCapabilityError,
    UnsupportedInputFormatError,
    UnsupportedParameterError,
)
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.logging import EventLogger
from aimedia.registry.events import log_model_validation_failed
from aimedia.registry.models import (
    CapabilityNode,
    EffectiveModelDefinition,
    ModelStatus,
    ParameterSpec,
    ParameterType,
)

# `final_format` не сверяется с enum Registry: это локальный конечный формат
# (baseline D08), а не provider output, и его проверяет локальный processing.
_REFERENCE_CAPABILITY = "reference_images"
_MULTIPLE_OUTPUTS_CAPABILITY = "multiple_outputs"
_MAX_IMAGES_PARAMETER = "max_images"


@dataclass(frozen=True, slots=True)
class ValidatedModelRequest:
    """Доменный запрос вместе с effective definition, выбранной для вызова.

    Adapter получает отсюда и сам нормализованный запрос, и binding:
    `remote_model_id` и `provider_id` находятся в `effective`, а не угадываются
    из доменного `ModelRef`.
    """

    request: ImageGenerationRequest
    effective: EffectiveModelDefinition

    @property
    def model_id(self) -> str:
        return self.effective.model_id

    @property
    def provider_id(self) -> str:
        return self.effective.provider_id

    @property
    def remote_model_id(self) -> str:
        return self.effective.remote_model_id


def validate_model_request(
    effective: EffectiveModelDefinition,
    request: ImageGenerationRequest,
    *,
    logger: EventLogger | None = None,
) -> ValidatedModelRequest:
    """Проверить request против effective definition и вернуть её для adapter.

    Ошибки — типизированные доменные: :class:`UnknownModelError`,
    :class:`UnknownProviderError`, :class:`UnsupportedParameterError`,
    :class:`InvalidParameterValueError`, :class:`TooManyReferenceImagesError`,
    :class:`UnsupportedCapabilityError`. Ни одна из них не является отказом
    provider: submit ещё не выполнялся.

    Необязательный `logger` включает событие `model_validation_failed` ровно на
    границе application: в запись попадают только код ошибки и имя параметра,
    без prompt, содержимого изображений и путей источников.
    """
    try:
        _validate(effective, request)
    except DomainError as exc:
        log_model_validation_failed(logger, exc)
        raise
    return ValidatedModelRequest(request=request, effective=effective)


def _validate(
    effective: EffectiveModelDefinition,
    request: ImageGenerationRequest,
) -> None:
    """Выполнить последовательность проверок request против effective definition."""
    _validate_binding(effective, request)
    if effective.status is ModelStatus.DISABLED:
        raise UnsupportedCapabilityError(
            f"Модель {effective.model_id!r} отключена и недоступна для генерации.",
            details={"model": effective.model_id, "status": effective.status.value},
        )
    parameters = effective.parameters

    _validate_enum_parameter(parameters, "resolution", request.resolution)
    _validate_enum_parameter(parameters, "aspect_ratio", request.aspect_ratio)
    _validate_enum_parameter(parameters, "quality", request.quality)
    _validate_enum_parameter(parameters, "output_format", request.output_format)
    _validate_numeric_parameter(parameters, "seed", request.seed)

    _validate_prompt(effective, request)
    _validate_reference_images(effective, len(request.images))
    _validate_image_formats(effective, request)
    _validate_max_images(effective, request.max_images)
    _validate_gpt_mie_settings(effective, request)


def _validate_gpt_mie_settings(
    effective: EffectiveModelDefinition, request: ImageGenerationRequest
) -> None:
    """Exact fixed-MIE catalog constraints; no generic conditional-rule language.

    Defaults still come from the effective definition. Source: GPT model page
    (2026-10-01): auto only at 1K; 1:1 unavailable at 4K.
    """
    if effective.provider_id != "polza" or effective.remote_model_id != "openai/gpt-5.4-image-2":
        return
    resolution_spec = effective.parameters.get("resolution")
    ratio_spec = effective.parameters.get("aspect_ratio")
    resolution = request.resolution or (resolution_spec.default if resolution_spec else None)
    ratio = request.aspect_ratio or (ratio_spec.default if ratio_spec else None)
    if (ratio == "auto" and resolution in {"2K", "4K"}) or (ratio == "1:1" and resolution == "4K"):
        raise InvalidParameterValueError(
            "Сочетание resolution/aspect_ratio не поддерживается GPT MIE.",
            details={"parameter": "aspect_ratio", "resolution": resolution, "aspect_ratio": ratio},
        )


def _validate_binding(
    effective: EffectiveModelDefinition,
    request: ImageGenerationRequest,
) -> None:
    """Сверить provider/model запроса с effective definition.

    Effective definition построена для конкретной пары model/provider; если
    доменный запрос указывает другую модель или provider, это несоответствие
    выбора, а не повод молча подставить чужой remote ID. Alias запроса допустим:
    effective definition сохраняет и запрошенное имя, и канонический ID.
    """
    if request.provider.id != effective.provider_id:
        raise UnknownProviderError(
            f"Provider {request.provider.id!r} не соответствует effective definition "
            f"{effective.provider_id!r}.",
            details={
                "provider": request.provider.id,
                "effective_provider": effective.provider_id,
            },
        )

    accepted = {effective.model_id, effective.requested_model, *effective.aliases}
    if request.model.id not in accepted:
        raise UnknownModelError(
            f"Модель {request.model.id!r} не соответствует effective definition "
            f"{effective.model_id!r}.",
            details={
                "model": request.model.id,
                "effective_model": effective.model_id,
            },
        )


def _validate_enum_parameter(
    parameters: Mapping[str, ParameterSpec],
    name: str,
    value: str | None,
) -> None:
    """Сверить значение со объявленным набором enum, если ограничение известно."""
    spec = parameters.get(name)
    if value is None:
        if spec is not None and spec.required and spec.default is None:
            raise InvalidParameterValueError(
                f"Обязательный параметр {name!r} отсутствует.",
                details={"parameter": name, "required": True},
            )
        # Documented default permits omission; validation does not mutate the request.
        return
    if spec is None:
        raise UnsupportedParameterError(
            f"Параметр {name!r} не объявлен моделью.",
            details={"parameter": name, "value": value},
        )
    if spec.max_length is not None and len(value) > spec.max_length:
        raise InvalidParameterValueError(
            f"Длина параметра {name!r} превышает документированный лимит.",
            details={"parameter": name, "length": len(value), "max_length": spec.max_length},
        )
    if spec.values is None:
        # Известный параметр без объявленного набора значений не ограничивает
        # конкретное значение: придумывать допустимый список запрещено.
        return
    if value not in spec.values:
        raise InvalidParameterValueError(
            f"Значение {value!r} недопустимо для параметра {name!r}.",
            details={"parameter": name, "value": value, "allowed": list(spec.values)},
        )


def _validate_numeric_parameter(
    parameters: Mapping[str, ParameterSpec],
    name: str,
    value: int | None,
) -> None:
    """Сверить числовое значение с объявленными границами параметра."""
    spec = parameters.get(name)
    if value is None:
        if spec is not None and spec.required and spec.default is None:
            raise InvalidParameterValueError(
                f"Обязательный параметр {name!r} отсутствует.",
                details={"parameter": name, "required": True},
            )
        return
    if spec is None:
        raise UnsupportedParameterError(
            f"Параметр {name!r} не объявлен моделью.",
            details={"parameter": name, "value": value},
        )
    if spec.type not in (ParameterType.INTEGER, ParameterType.NUMBER):
        raise UnsupportedParameterError(
            f"Параметр {name!r} модели не является числовым.",
            details={"parameter": name, "type": spec.type.value},
        )
    if spec.min is not None and value < spec.min:
        raise InvalidParameterValueError(
            f"Значение {value!r} для параметра {name!r} меньше допустимого {spec.min!r}.",
            details={"parameter": name, "value": value, "min": spec.min},
        )
    if spec.max is not None and value > spec.max:
        raise InvalidParameterValueError(
            f"Значение {value!r} для параметра {name!r} больше допустимого {spec.max!r}.",
            details={"parameter": name, "value": value, "max": spec.max},
        )


def _validate_prompt(effective: EffectiveModelDefinition, request: ImageGenerationRequest) -> None:
    """Ограничить длину отправляемого compiled snapshot, не раскрывая текст."""
    prompt_input = effective.inputs.get("prompt")
    if prompt_input is None:
        return
    if prompt_input.supported is False:
        raise UnsupportedCapabilityError(
            "Модель не поддерживает prompt для image generation.",
            details={"parameter": "prompt"},
        )
    if prompt_input.max_chars is None:
        return
    length = len(request.prompt.text)
    if length > prompt_input.max_chars:
        raise InvalidParameterValueError(
            "Длина prompt превышает документированный лимит модели.",
            details={"parameter": "prompt", "length": length, "max_chars": prompt_input.max_chars},
        )


def _validate_image_formats(
    effective: EffectiveModelDefinition, request: ImageGenerationRequest
) -> None:
    """Сверить MIME подготовленных references с документированными форматами."""
    image_input = effective.inputs.get("images")
    if image_input is None or image_input.formats is None:
        return
    allowed = {f"image/{fmt}" for fmt in image_input.formats}
    for image in request.images:
        if image.mime_type not in allowed:
            raise UnsupportedInputFormatError(
                "Формат reference image не принимается моделью.",
                details={
                    "parameter": "--image",
                    "mime_type": image.mime_type,
                    "allowed_formats": list(image_input.formats),
                },
            )


def _validate_reference_images(effective: EffectiveModelDefinition, count: int) -> None:
    """Проверить все документированные границы reference images из effective definition."""
    cap = effective.capabilities.get(_REFERENCE_CAPABILITY)
    image_input = effective.inputs.get("images")
    if count and (
        cap is False
        or (isinstance(cap, CapabilityNode) and not cap.supported)
        or (image_input is not None and image_input.supported is False)
    ):
        raise UnsupportedCapabilityError(
            "Модель не поддерживает reference images.",
            details={"parameter": "--image", "requested": count},
        )

    maximums = [
        bound
        for bound in (
            cap.max if isinstance(cap, CapabilityNode) else None,
            image_input.max if image_input is not None else None,
        )
        if bound is not None
    ]
    minimums = [
        bound
        for bound in (
            cap.min if isinstance(cap, CapabilityNode) else None,
            image_input.min if image_input is not None else None,
        )
        if bound is not None
    ]
    maximum = min(maximums) if maximums else None
    if maximum is not None and count > maximum:
        raise TooManyReferenceImagesError(
            f"Запрошено {count} reference images, допустимо не более {maximum}.",
            details={"requested": count, "max_references": maximum},
        )
    minimum = max(minimums) if minimums else None
    if image_input is not None and image_input.required is True:
        minimum = max(minimum or 0, 1)
    if minimum is not None and count < minimum:
        raise InvalidParameterValueError(
            f"Запрошено {count} reference images, требуется не менее {minimum}.",
            details={"parameter": "--image", "requested": count, "min_references": minimum},
        )


def _validate_max_images(effective: EffectiveModelDefinition, max_images: int) -> None:
    """Проверить число выходных изображений по документированным границам модели."""
    spec = effective.parameters.get(_MAX_IMAGES_PARAMETER)
    if spec is not None:
        if spec.min is not None and max_images < spec.min:
            raise InvalidParameterValueError(
                f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
                f"меньше допустимого {spec.min!r}.",
                details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images, "min": spec.min},
            )
        if spec.max is not None and max_images > spec.max:
            raise InvalidParameterValueError(
                f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
                f"больше допустимого {spec.max!r}.",
                details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images, "max": spec.max},
            )

    image_output = effective.outputs.get("images")
    if image_output is not None:
        if image_output.supported is False:
            raise UnsupportedCapabilityError(
                "Модель не поддерживает image outputs.",
                details={"parameter": _MAX_IMAGES_PARAMETER},
            )
        if image_output.max is not None and max_images > image_output.max:
            raise InvalidParameterValueError(
                f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
                f"больше допустимого {image_output.max!r}.",
                details={
                    "parameter": _MAX_IMAGES_PARAMETER,
                    "value": max_images,
                    "max": image_output.max,
                },
            )
        if image_output.min is not None and max_images < image_output.min:
            raise InvalidParameterValueError(
                f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
                f"меньше допустимого {image_output.min!r}.",
                details={
                    "parameter": _MAX_IMAGES_PARAMETER,
                    "value": max_images,
                    "min": image_output.min,
                },
            )

    cap = effective.capabilities.get(_MULTIPLE_OUTPUTS_CAPABILITY)
    if cap is None:
        return
    if isinstance(cap, bool):
        if not cap and max_images > 1:
            raise UnsupportedCapabilityError(
                "Модель не поддерживает несколько выходных изображений.",
                details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images},
            )
        return
    if not cap.supported and max_images > 1:
        raise UnsupportedCapabilityError(
            "Модель не поддерживает несколько выходных изображений.",
            details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images},
        )
    if cap.max is not None and max_images > cap.max:
        raise InvalidParameterValueError(
            f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
            f"больше допустимого {cap.max!r}.",
            details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images, "max": cap.max},
        )
    if cap.min is not None and max_images < cap.min:
        raise InvalidParameterValueError(
            f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
            f"меньше допустимого {cap.min!r}.",
            details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images, "min": cap.min},
        )


__all__ = [
    "ValidatedModelRequest",
    "validate_model_request",
]
