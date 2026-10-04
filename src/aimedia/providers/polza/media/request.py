"""Чистое преобразование доменного image request в тело POST /v1/media Polza.

Этот модуль — только mapping, без HTTP: ни клиента, ни ответов, ни ошибок
provider (они появятся на C09b/C09c). :func:`build_media_request` строит ровно те
поля, которые подтверждены схемой `POST /v1/media` (`docs/Post Media.txt`) и
effective definition модели; произвольная передача `provider_options` запрещена.

Инварианты:

- `model` — это `effective.remote_model_id`, а не логический ID или alias домена;
  `provider_id` и alias в запрос не попадают;
- в `input.prompt` уходит точный текст compiled prompt без переписывания;
- reference images идут в исходном порядке как base64 data URI с **фактическим**
  MIME, определённым по байтам (`probe_image`), а не по объявленному `InputRef`;
  объявленные размер и SHA-256 обязаны совпасть до кодирования, иначе запрос
  отклоняется до HTTP;
- локальный `final_format` никогда не попадает в provider payload;
- `max_images` всегда уходит, когда модель объявляет точное поле (включая
  доменное значение `1`, независимо от model default); без поля `1` опускается,
  а значения больше `1` отклоняются; граница generic схемы Polza — не более 6.
  Exact GPT-5.4 Image 2 и GPT Image 2.5 с fixed MIE routing используют Media
  input.max_images=1 по CN-05 (references — отдельные входы);
- обязательные устойчивые поля и поддерживаемые provider options с документированным
  default передаются явно, без default — отклоняются до HTTP;
- exact fixed-MIE bindings получают top-level boolean async=true до final cap;
  generic mapping и caller provider_options не управляют этим правилом;
- размер тела ограничен **явным локальным** safety-cap: это защита проекта, а не
  документированный лимит Polza или конкретной модели.

Ни одно сообщение об ошибке и `details` не содержат путь, байты, base64 или текст
prompt: остаются только имя параметра, позиция и числа/хеши.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Sequence
from decimal import ROUND_CEILING, Decimal

from aimedia.application.inputs.image_probe import (
    SUPPORTED_IMAGE_MIME_TYPES,
    InvalidImageContentError,
    probe_image,
)
from aimedia.domain.errors import (
    InvalidParameterValueError,
    UnsupportedCapabilityError,
    UnsupportedInputFormatError,
    UnsupportedParameterError,
)
from aimedia.domain.inputs import InputKind, InputRef
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.registry.models import EffectiveModelDefinition, ParameterSpec, ParameterType
from aimedia.registry.validator import ValidatedModelRequest

_REFERENCE_PARAMETER = "--image"
_MAX_IMAGES_PARAMETER = "max_images"
# ImageInputDto constrains this field independently of model-specific limits.
_MAX_SCHEMA_IMAGES = 6
# Canonical Media model identity; MIE is selected only by the fixed ProviderDto.
_GPT_MIE_MODEL = "openai/gpt-5.4-image-2"
_GPT_MIE_MODELS = frozenset(
    {_GPT_MIE_MODEL, "openai/gpt-image-2.5-sunburst", "openai/gpt-image-2.5-flare"}
)
# MediaRequestDto.provider selects upstreams without inventing model qualifiers.
_MIE_PRICED_MODELS = frozenset(
    {"qwen/image-2.1", "google/gemini-3.1-flash-image-preview", *_GPT_MIE_MODELS}
)
_SAFE_REMOTE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}\Z")
_STABLE_SCHEMA_ENUMS = {
    "aspect_ratio": (
        "1:1",
        "1:4",
        "4:1",
        "1:8",
        "8:1",
        "2:3",
        "3:2",
        "3:4",
        "4:3",
        "4:5",
        "5:4",
        "9:16",
        "16:9",
        "21:9",
        "9:21",
        # ImageInputDto OpenAPI and GPT Image 2.5 catalog, 2026-10-04.
        "27:16",
        "16:27",
        "9:8",
        "8:9",
        "auto",
    ),
    "resolution": ("0.5K", "1K", "2K", "4K"),
    "quality": ("basic", "medium", "high"),
    "output_format": ("png", "jpeg", "webp"),
}
_STABLE_FIELDS = (
    ("aspect_ratio", "aspect_ratio"),
    ("resolution", "image_resolution"),
    ("seed", "seed"),
    ("quality", "quality"),
    ("output_format", "output_format"),
)
# Только неустойчивые скалярные поля ImageInputDto (`docs/Post Media.txt`).
# URL, вложенные файлы и устойчивые поля разрешаются только через отдельный mapping.
_OPTION_TYPES: dict[str, type] = {
    "watermark": str,
    "isEnhance": bool,
    "guidance_scale": float,
    "strength": float,
    "enable_safety_checker": bool,
    "upscale_factor": str,
}
_OPTION_ENUMS: dict[str, tuple[str, ...]] = {"upscale_factor": ("1", "2", "4", "8")}

# Каноническая сериализация для точного размера тела: компактные разделители без
# пробелов, unicode не экранируется. Единственный владелец вида байтов —
# :func:`serialize_media_request`; транспорт C09c обязан отправлять именно их,
# иначе гарантия по размеру тела не совпадёт.
_JSON_SEPARATORS: tuple[str, str] = (",", ":")


def build_media_request(
    validated: ValidatedModelRequest,
    reference_bytes: Sequence[bytes],
    *,
    max_body_bytes: int,
) -> dict[str, object]:
    """Построить JSON-payload для `POST /v1/media` из проверенного доменного запроса.

    `validated` уже прошёл :func:`~aimedia.registry.validator.validate_model_request`
    против effective definition, поэтому здесь только mapping. `reference_bytes`
    — байты reference images в том же порядке, что `validated.request.images`:
    каждый блок сверяется с объявленными `size_bytes`/`sha256` и распознаётся
    `probe_image` до кодирования.

    `max_body_bytes` — обязательный **локальный** safety-cap на размер тела.
    Он проверяется дважды: до кодирования по нижней оценке (размер prompt плюс
    длина base64) и после по точным сериализованным байтам. Это ограничение
    проекта, а не документированный лимит Polza или модели.

    Ошибки до HTTP: :class:`InvalidParameterValueError` (несовпадение числа,
    размера или hash входов; превышение локального safety-cap),
    :class:`UnsupportedInputFormatError` (байты не распознаны как поддерживаемое
    изображение или фактический MIME не совпал с объявленным),
    :class:`UnsupportedCapabilityError` (запрошено несколько изображений, но
    модель их не объявляет). `ValueError` — некорректный `max_body_bytes`.
    """
    if isinstance(max_body_bytes, bool) or max_body_bytes <= 0:
        raise ValueError("max_body_bytes должен быть положительным локальным safety-лимитом")

    request = validated.request
    effective = validated.effective
    _validate_required_parameters(request, effective)

    _enforce_estimated_size(
        request.prompt.text,
        reference_bytes,
        max_body_bytes=max_body_bytes,
    )
    images = _encode_reference_images(request.images, reference_bytes)

    input_payload: dict[str, object] = {"prompt": request.prompt.text}
    if images:
        input_payload["images"] = images
    _apply_declared_parameters(request, effective, input_payload)
    _apply_provider_options(request, effective, input_payload)

    payload: dict[str, object] = {
        "model": effective.remote_model_id,
        "input": input_payload,
    }
    if effective.remote_model_id in _MIE_PRICED_MODELS:
        payload["provider"] = _build_mie_price_filter(effective)
        payload["async"] = True
    _enforce_exact_body_size(payload, max_body_bytes=max_body_bytes)
    if not _SAFE_REMOTE_MODEL_ID.fullmatch(effective.remote_model_id):
        raise InvalidParameterValueError(
            "Недопустимый remote model ID или неподтверждённый qualifier Polza.",
            details={"parameter": "remote_model_id"},
        )
    return payload


def _build_mie_price_filter(effective: EffectiveModelDefinition) -> dict[str, object]:
    """Fixed documented MIE route and RUB/image API filter, never actual billing."""
    pricing = effective.pricing
    if (
        pricing is None
        or pricing.currency != "RUB"
        or not pricing.by_resolution
        or any(
            not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0
            for amount in pricing.by_resolution.values()
        )
    ):
        raise InvalidParameterValueError(
            "Для фиксированного маршрута Polza требуется пригодная опубликованная цена RUB.",
            details={"parameter": "pricing"},
        )
    ceiling = int(pricing.published_max.to_integral_value(rounding=ROUND_CEILING))
    return {"only": ["mie"], "allow_fallbacks": False, "max_price": {"image": ceiling}}


def _encode_reference_images(
    refs: Sequence[InputRef],
    reference_bytes: Sequence[bytes],
) -> list[dict[str, str]]:
    """Закодировать reference images в порядке запроса как base64 data URI."""
    if len(refs) != len(reference_bytes):
        raise InvalidParameterValueError(
            "Число reference images не совпадает с числом переданных байтов.",
            details={
                "parameter": _REFERENCE_PARAMETER,
                "declared": len(refs),
                "provided": len(reference_bytes),
            },
        )
    encoded: list[dict[str, str]] = []
    for position, (ref, content) in enumerate(zip(refs, reference_bytes, strict=True)):
        mime_type = _verify_reference(ref, content, position=position)
        data = base64.b64encode(content).decode("ascii")
        encoded.append({"type": "base64", "data": f"data:{mime_type};base64,{data}"})
    return encoded


def _verify_reference(ref: InputRef, content: bytes, *, position: int) -> str:
    """Сверить байты с подготовленным `InputRef` и вернуть фактический MIME.

    Проверяется, что это image-вход, что объявленные размер и SHA-256 совпадают с
    фактическими байтами и что содержимое распознаётся как поддерживаемое
    изображение. Доверять только расширению или объявленному MIME нельзя:
    фактический MIME берётся из `probe_image`, а расхождение с объявленным —
    ошибка, а не молчаливая подмена.
    """
    details: dict[str, object] = {"parameter": _REFERENCE_PARAMETER, "position": position}
    if ref.kind is not InputKind.IMAGE:
        raise InvalidParameterValueError(
            "Поддерживаются только image reference inputs.",
            details={**details, "kind": ref.kind.value},
        )
    if ref.size_bytes is None or ref.sha256 is None:
        raise InvalidParameterValueError(
            "Подготовленный reference image не содержит размера и hash для проверки.",
            details=details,
        )
    if len(content) != ref.size_bytes:
        raise InvalidParameterValueError(
            "Размер байтов reference image не совпадает с подготовленным.",
            details={
                **details,
                "declared_size_bytes": ref.size_bytes,
                "provided_bytes": len(content),
            },
        )
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != ref.sha256:
        raise InvalidParameterValueError(
            "SHA-256 байтов reference image не совпадает с подготовленным.",
            details={
                **details,
                "declared_sha256": ref.sha256,
                "actual_sha256": actual_sha256,
            },
        )
    try:
        probe = probe_image(content)
    except InvalidImageContentError:
        # Не связывать ошибку с исходным parser exception: он может содержать
        # пользовательское имя PNG chunk'а в traceback.
        probe = None
    if probe is None:
        raise UnsupportedInputFormatError(
            "Байты reference image не распознаны как поддерживаемое изображение.",
            details=details,
        )
    if probe.mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise UnsupportedInputFormatError(
            "Фактический формат reference image не поддерживается локально.",
            details={**details, "detected_mime_type": probe.mime_type},
        )
    if ref.mime_type is not None and ref.mime_type != probe.mime_type:
        raise UnsupportedInputFormatError(
            "Объявленный MIME reference image не совпадает с фактическим.",
            details={
                **details,
                "declared_mime_type": ref.mime_type,
                "detected_mime_type": probe.mime_type,
            },
        )
    return probe.mime_type


def _validate_required_parameters(
    request: ImageGenerationRequest, effective: EffectiveModelDefinition
) -> None:
    """Не игнорировать required поля, которые этот mapper не умеет передать."""
    represented = {
        "prompt",
        "images",
        _MAX_IMAGES_PARAMETER,
        *(_STABLE_SCHEMA_ENUMS),
        "seed",
        *_OPTION_TYPES,
    }
    for name, spec in effective.parameters.items():
        if not spec.required:
            continue
        if name not in represented:
            raise UnsupportedParameterError(
                "Обязательный параметр модели не представлен в Polza image request.",
                details={"parameter": "model_parameters", "required": True},
            )
        if name == "images" and not request.images:
            raise InvalidParameterValueError(
                "Обязательные reference images отсутствуют.",
                details={"parameter": "--image", "required": True},
            )


def _valid_stable_value(name: str, value: object) -> bool:
    """Проверить глобальную схему ImageInputDto поверх model-specific validation."""
    if name == "seed":
        return type(value) is int
    return isinstance(value, str) and value in _STABLE_SCHEMA_ENUMS[name]


def _apply_declared_parameters(
    request: ImageGenerationRequest,
    effective: EffectiveModelDefinition,
    input_payload: dict[str, object],
) -> None:
    """Перенести устойчивые доменные параметры в поля Polza `input`.

    Каждый не-None параметр уже проверен validator'ом как объявленный моделью.
    Обязательный default материализуется явно только если представим в ImageInputDto.
    Переименование `resolution → image_resolution` выполняет adapter
    (`docs/plans/05-provider-system.md`, «Почему Adapter не может просто передать
    весь request как есть»). Локальный `final_format` сюда не входит.
    """
    for name, provider_field in _STABLE_FIELDS:
        value = getattr(request, name)
        spec = effective.parameters.get(name)
        using_default = value is None and spec is not None and spec.required
        if value is None and spec is not None and spec.required:
            value = spec.default
        if value is None and not using_default:
            continue
        if not _valid_stable_value(name, value):
            raise InvalidParameterValueError(
                "Параметр модели не может быть передан в Polza ImageInputDto.",
                details={"parameter": name, **({"required": True} if using_default else {})},
            )
        input_payload[provider_field] = value
    if effective.remote_model_id in _GPT_MIE_MODELS:
        if request.max_images != 1:
            raise InvalidParameterValueError(
                "Новый GPT MIE запрос поддерживает один результат (CN-05).",
                details={"parameter": _MAX_IMAGES_PARAMETER, "min": 1, "max": 1},
            )
        # Media ImageInputDto field; the prior model-guide n does not prove count.
        input_payload[_MAX_IMAGES_PARAMETER] = request.max_images
    elif _include_max_images(request.max_images, effective):
        input_payload[_MAX_IMAGES_PARAMETER] = request.max_images


def _include_max_images(max_images: int, effective: EffectiveModelDefinition) -> bool:
    """Передать запрошенное число при объявленном поле, не полагаясь на API default.

    Только точное объявление поля `max_images` доказывает поддержку поля API.
    Capability `multiple_outputs` описывает результат, но не синтаксис запроса.
    Даже объявленная моделью граница не может расширить максимум ImageInputDto.
    """
    if max_images > _MAX_SCHEMA_IMAGES:
        raise InvalidParameterValueError(
            "max_images превышает границу схемы Polza ImageInputDto.",
            details={"parameter": _MAX_IMAGES_PARAMETER, "max": _MAX_SCHEMA_IMAGES},
        )
    if _MAX_IMAGES_PARAMETER in effective.parameters:
        return True
    if max_images == 1:
        return False
    raise UnsupportedCapabilityError(
        "Модель не объявляет поддержку нескольких изображений; max_images > 1 не отправляется.",
        details={"parameter": _MAX_IMAGES_PARAMETER, "value": max_images},
    )


def _apply_provider_options(
    request: ImageGenerationRequest,
    effective: EffectiveModelDefinition,
    input_payload: dict[str, object],
) -> None:
    """Перенести только подтверждённые моделью `provider_options`.

    Registry validator не проверяет provider_options: каждый ключ обязан быть
    скалярным полем ImageInputDto И объявлен в effective parameters. Обязательные
    options материализуют документированный допустимый default или завершаются
    ошибкой до HTTP. Ошибки не включают пользовательские ключи или значения.
    """
    for key, spec in effective.parameters.items():
        if key not in _OPTION_TYPES or not spec.required:
            continue
        if request.provider_options.get(key) is None:
            if spec.default is None or not _valid_option(key, spec.default, spec):
                raise InvalidParameterValueError(
                    "Обязательный provider_options не может быть передан в Polza.",
                    details={"parameter": "provider_options", "required": True},
                )
            input_payload[key] = spec.default

    for key, value in request.provider_options.items():
        option_spec = effective.parameters.get(key)
        if key not in _OPTION_TYPES or option_spec is None:
            raise UnsupportedParameterError(
                "Параметр provider_options не поддерживается для этой модели.",
                details={"parameter": "provider_options"},
            )
        if value is None:
            continue
        if not _valid_option(key, value, option_spec):
            raise InvalidParameterValueError(
                "Недопустимое значение provider_options.",
                details={"parameter": "provider_options"},
            )
        input_payload[key] = value


def _valid_option(key: str, value: object, spec: ParameterSpec) -> bool:
    """Проверить пересечение типа/ограничений схемы Polza и effective модели."""
    expected = _OPTION_TYPES[key]
    if expected is str:
        if not isinstance(value, str) or spec.type not in (
            ParameterType.STRING,
            ParameterType.ENUM,
        ):
            return False
        if key in _OPTION_ENUMS and value not in _OPTION_ENUMS[key]:
            return False
        if spec.max_length is not None and len(value) > spec.max_length:
            return False
    elif expected is bool:
        if type(value) is not bool or spec.type not in (ParameterType.BOOLEAN, ParameterType.ENUM):
            return False
    else:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or spec.type
            not in (
                ParameterType.NUMBER,
                ParameterType.INTEGER,
                ParameterType.ENUM,
            )
        ):
            return False
        if spec.type is ParameterType.INTEGER and type(value) is not int:
            return False
        try:
            if not math.isfinite(value):
                return False
        except OverflowError:
            return False
        if key == "strength" and not 0 <= value <= 1:
            return False
    if spec.type is ParameterType.ENUM and (
        spec.values is None
        or not any(
            type(value) is type(candidate) and value == candidate for candidate in spec.values
        )
    ):
        return False
    if spec.min is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < spec.min:
            return False
    if spec.max is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > spec.max:
            return False
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return False
    return True


def serialize_media_request(payload: dict[str, object]) -> bytes:
    """Сериализовать payload `POST /v1/media` в канонические байты тела.

    Единственная реализация сериализации: :func:`build_media_request` проверяет по
    этим байтам локальный safety-cap, а транспорт C09c отправляет ровно их без
    второго `json.dumps`. Компактные разделители без пробелов, unicode не
    экранируется — форма стабильна между проверкой и отправкой.

    Некодируемое в UTF-8 тело становится :class:`InvalidParameterValueError` без
    исходной причины: текст или имя не попадают в traceback.
    """
    body: bytes | None = None
    unencodable = False
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS).encode("utf-8")
    except UnicodeError:
        unencodable = True
    if unencodable or body is None:
        raise InvalidParameterValueError(
            "Тело запроса не кодируется в UTF-8.",
            details={"parameter": "body"},
        )
    return body


def _base64_encoded_length(size_bytes: int) -> int:
    """Длина base64 для блока из `size_bytes` байт без учёта padding-строки."""
    return 4 * ((size_bytes + 2) // 3)


def _enforce_estimated_size(
    prompt_text: str,
    reference_bytes: Sequence[bytes],
    *,
    max_body_bytes: int,
) -> None:
    """Отклонить заведомо слишком большой запрос до кодирования base64.

    Оценка — нижняя граница фактического тела (prompt плюс длина base64), поэтому
    её превышение гарантирует превышение лимита и не даёт ложных отказов на
    границе. Точное значение проверяется после сериализации.
    """
    try:
        prompt_bytes = len(prompt_text.encode("utf-8"))
    except UnicodeError:
        prompt_bytes = None
    if prompt_bytes is None:
        raise InvalidParameterValueError(
            "Текст prompt не кодируется в UTF-8 для запроса.",
            details={"parameter": "prompt"},
        )
    estimate = prompt_bytes + sum(_base64_encoded_length(len(block)) for block in reference_bytes)
    if estimate > max_body_bytes:
        raise InvalidParameterValueError(
            "Ожидаемый размер тела запроса превышает локальный safety-лимит; "
            "это ограничение проекта, а не документированный лимит Polza.",
            details={
                "parameter": "body",
                "max_bytes": max_body_bytes,
                "estimated_bytes": estimate,
            },
        )


def _enforce_exact_body_size(payload: dict[str, object], *, max_body_bytes: int) -> None:
    """Отклонить запрос по точному размеру канонически сериализованного тела."""
    body_bytes = len(serialize_media_request(payload))
    if body_bytes > max_body_bytes:
        raise InvalidParameterValueError(
            "Размер тела запроса превышает локальный safety-лимит; "
            "это ограничение проекта, а не документированный лимит Polza.",
            details={
                "parameter": "body",
                "max_bytes": max_body_bytes,
                "body_bytes": body_bytes,
            },
        )


__all__ = ["build_media_request", "serialize_media_request"]
