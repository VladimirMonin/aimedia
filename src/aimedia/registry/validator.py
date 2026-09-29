"""Проверка запроса против Effective Model Definition (E03, C05b).

Validator — отдельная обязанность от resolver (`06-model-registry.md`,
«Validator»): он работает только с уже построенной effective definition и
типизированным доменным запросом, не читает YAML и не ходит в сеть. Порядок
проверок повторяет документированный flow: binding к provider, ненулевые
capability, enum-параметры, числовые границы, число reference images.

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
параметра, запрошенное значение, документированный набор/граница и число
reference images.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aimedia.domain.errors import (
    InvalidParameterValueError,
    TooManyReferenceImagesError,
    UnknownModelError,
    UnknownProviderError,
    UnsupportedCapabilityError,
    UnsupportedParameterError,
)
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.registry.models import (
    CapabilityNode,
    EffectiveModelDefinition,
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
) -> ValidatedModelRequest:
    """Проверить request против effective definition и вернуть её для adapter.

    Ошибки — типизированные доменные: :class:`UnknownModelError`,
    :class:`UnknownProviderError`, :class:`UnsupportedParameterError`,
    :class:`InvalidParameterValueError`, :class:`TooManyReferenceImagesError`,
    :class:`UnsupportedCapabilityError`. Ни одна из них не является отказом
    provider: submit ещё не выполнялся.
    """
    _validate_binding(effective, request)
    parameters = effective.parameters

    _validate_enum_parameter(parameters, "resolution", request.resolution)
    _validate_enum_parameter(parameters, "aspect_ratio", request.aspect_ratio)
    _validate_enum_parameter(parameters, "quality", request.quality)
    _validate_enum_parameter(parameters, "output_format", request.output_format)
    _validate_numeric_parameter(parameters, "seed", request.seed)

    _validate_reference_images(effective.capabilities, len(request.images))
    _validate_max_images(effective, request.max_images)

    return ValidatedModelRequest(request=request, effective=effective)


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
    if value is None:
        return
    spec = parameters.get(name)
    if spec is None:
        raise UnsupportedParameterError(
            f"Параметр {name!r} не объявлен моделью.",
            details={"parameter": name, "value": value},
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
    if value is None:
        return
    spec = parameters.get(name)
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


def _validate_reference_images(
    capabilities: Mapping[str, bool | CapabilityNode],
    count: int,
) -> None:
    """Проверить число reference images по capability модели.

    Отсутствующая capability означает неизвестное ограничение: число не
    выдумывается, и произвольное количество не отклоняется. Известная `supported:
    true` без `max` тоже не ограничивает число. В `details` попадает только
    количество, без содержимого изображений.
    """
    cap = capabilities.get(_REFERENCE_CAPABILITY)
    if cap is None:
        return
    if isinstance(cap, bool):
        if not cap and count:
            raise UnsupportedCapabilityError(
                "Модель не поддерживает reference images.",
                details={"parameter": "--image", "requested": count},
            )
        return
    if not cap.supported:
        if count:
            raise UnsupportedCapabilityError(
                "Модель не поддерживает reference images.",
                details={"parameter": "--image", "requested": count},
            )
        return
    if cap.max is not None and count > cap.max:
        raise TooManyReferenceImagesError(
            f"Запрошено {count} reference images, допустимо не более {cap.max}.",
            details={"requested": count, "max_references": cap.max},
        )
    if cap.min is not None and count < cap.min:
        raise InvalidParameterValueError(
            f"Запрошено {count} reference images, требуется не менее {cap.min}.",
            details={"parameter": "--image", "requested": count, "min_references": cap.min},
        )


def _validate_max_images(effective: EffectiveModelDefinition, max_images: int) -> None:
    """Проверить число выходных изображений по параметру или capability модели."""
    spec = effective.parameters.get(_MAX_IMAGES_PARAMETER)
    if spec is not None and spec.max is not None and max_images > spec.max:
        raise InvalidParameterValueError(
            f"Значение {max_images!r} для параметра {_MAX_IMAGES_PARAMETER!r} "
            f"больше допустимого {spec.max!r}.",
            details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images, "max": spec.max},
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


__all__ = [
    "ValidatedModelRequest",
    "validate_model_request",
]
