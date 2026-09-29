"""Контрактные тесты чистого Polza media request mapper (E06, C09a).

Проверяется точное тело `POST /v1/media`, которое строит
`aimedia.providers.polza.media.request.build_media_request` — без HTTP-клиента,
ответов и ошибок provider (они относятся к C09b/C09c).

Все модельные записи синтетические: реальные Polza model IDs и лимиты на C09a не
подтверждены и не выдумываются (`docs/plans/README.md`, E06). Ни один тест не
выполняет сетевой запрос: offline-guard из `tests/conftest.py` блокирует любой
внешний сокет, а отдельный тест доказывает, что модуль mapper'а не импортирует
транспорт.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import traceback
from pathlib import Path

import pytest
from image_fixtures import jpeg_bytes, png_bytes, webp_lossy_bytes

from aimedia.application.inputs.image_probe import InvalidImageContentError, probe_image
from aimedia.domain import (
    CompiledPrompt,
    FinalFormat,
    ImageGenerationRequest,
    InputKind,
    InputRef,
    InvalidParameterValueError,
    ModelRef,
    ProviderRef,
    UnsupportedCapabilityError,
    UnsupportedInputFormatError,
    UnsupportedParameterError,
)
from aimedia.providers.polza.media import build_media_request
from aimedia.registry import (
    CapabilityNode,
    InputLimit,
    ModelResolver,
    ModelStatus,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord

PROMPT_TEXT = "synthetic prompt"
SYNTHETIC_REMOTE_ID = "synthetic/remote"
MODEL_ID = "synthetic-image"
PROVIDER_ID = "polza"
HUGE_BODY_BYTES = 64 * 1024 * 1024

REQUEST_MODULE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "aimedia"
    / "providers"
    / "polza"
    / "media"
    / "request.py"
)


def _parameters() -> dict[str, ParameterSpec]:
    """Набор документированных параметров синтетической модели."""
    return {
        "resolution": ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K", "4K")),
        "aspect_ratio": ParameterSpec(type=ParameterType.ENUM, values=("1:1", "16:9", "9:16")),
        "seed": ParameterSpec(type=ParameterType.INTEGER, min=0, max=2**32 - 1),
        "quality": ParameterSpec(type=ParameterType.ENUM, values=("basic", "medium", "high")),
        "output_format": ParameterSpec(type=ParameterType.ENUM, values=("png", "jpeg", "webp")),
        "max_images": ParameterSpec(type=ParameterType.INTEGER, min=1, max=6),
    }


def _effective(
    *,
    parameters: dict[str, ParameterSpec] | None = None,
    capabilities: dict[str, bool | CapabilityNode] | None = None,
    inputs: dict[str, InputLimit] | None = None,
):
    """Синтетическая effective definition для provider `polza`."""
    record = ModelRecord(
        schema_version=1,
        model_id=MODEL_ID,
        name="Synthetic image",
        family="image",
        status=ModelStatus.ACTIVE,
        capabilities=capabilities or {},
        inputs=inputs or {},
        outputs={},
        parameters=parameters or {},
        providers={PROVIDER_ID: ProviderBinding(remote_model_id=SYNTHETIC_REMOTE_ID)},
    )
    return ModelResolver([record]).resolve(MODEL_ID, PROVIDER_ID)


def _request(
    *,
    images: list[InputRef] | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    seed: int | None = None,
    quality: str | None = None,
    output_format: str | None = None,
    final_format: FinalFormat | None = None,
    max_images: int = 1,
    provider_options: dict[str, object] | None = None,
) -> ImageGenerationRequest:
    return ImageGenerationRequest(
        provider=ProviderRef(id=PROVIDER_ID),
        model=ModelRef(id=MODEL_ID),
        prompt=CompiledPrompt(text=PROMPT_TEXT, source_count=1),
        images=images if images is not None else [],
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        seed=seed,
        quality=quality,
        output_format=output_format,
        final_format=final_format,
        max_images=max_images,
        provider_options=provider_options if provider_options is not None else {},
    )


def _input_ref(
    path: str,
    content: bytes,
    *,
    position: int,
    mime_type: str | None = None,
) -> InputRef:
    """Подготовленный reference image с фактическими размером и SHA-256."""
    return InputRef(
        kind=InputKind.IMAGE,
        path=Path(path),
        position=position,
        mime_type=mime_type if mime_type is not None else probe_image(content).mime_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _data_uri(content: bytes) -> str:
    mime_type = probe_image(content).mime_type
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def test_maps_exact_payload_and_field_order() -> None:
    """Полный запрос превращается в точный payload с фиксированным порядком полей."""
    png = png_bytes()
    jpeg = jpeg_bytes()
    effective = _effective(
        parameters=_parameters(),
        capabilities={"reference_images": CapabilityNode(supported=True, max=4)},
        inputs={"images": InputLimit(supported=True, max=4, formats=("png", "jpeg", "webp"))},
    )
    request = _request(
        images=[
            _input_ref("ref-a.png", png, position=0),
            _input_ref("ref-b.jpg", jpeg, position=1),
        ],
        resolution="2K",
        aspect_ratio="16:9",
        seed=0,
        quality="high",
        output_format="png",
        final_format=FinalFormat.WEBP,
        max_images=4,
    )
    validated = validate_model_request(effective, request)

    payload = build_media_request(validated, [png, jpeg], max_body_bytes=HUGE_BODY_BYTES)

    assert payload == {
        "model": SYNTHETIC_REMOTE_ID,
        "input": {
            "prompt": PROMPT_TEXT,
            "images": [
                {"type": "base64", "data": _data_uri(png)},
                {"type": "base64", "data": _data_uri(jpeg)},
            ],
            "aspect_ratio": "16:9",
            "image_resolution": "2K",
            "seed": 0,
            "quality": "high",
            "output_format": "png",
            "max_images": 4,
        },
    }
    assert list(payload) == ["model", "input"]
    assert list(payload["input"]) == [
        "prompt",
        "images",
        "aspect_ratio",
        "image_resolution",
        "seed",
        "quality",
        "output_format",
        "max_images",
    ]


def test_seed_zero_is_sent_and_none_is_omitted() -> None:
    """Нулевой seed значим и уходит; отсутствующий seed не отправляется."""
    effective = _effective(parameters=_parameters())
    with_zero = validate_model_request(effective, _request(seed=0))
    payload_zero = build_media_request(with_zero, [], max_body_bytes=HUGE_BODY_BYTES)
    assert payload_zero["input"]["seed"] == 0

    without = validate_model_request(effective, _request(seed=None))
    payload_none = build_media_request(without, [], max_body_bytes=HUGE_BODY_BYTES)
    assert "seed" not in payload_none["input"]


def test_local_final_format_never_reaches_provider_payload() -> None:
    """Локальный конечный формат (включая WebP) не является полем provider."""
    effective = _effective()
    request = _request(final_format=FinalFormat.WEBP, output_format=None)
    validated = validate_model_request(effective, request)

    payload = build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    assert payload["input"] == {"prompt": PROMPT_TEXT}
    assert "final_format" not in payload["input"]


def test_optional_fields_are_omitted_when_absent() -> None:
    """None-параметры и непринятый default `max_images=1` не попадают в тело."""
    effective = _effective()
    request = _request()
    validated = validate_model_request(effective, request)

    payload = build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    assert payload["input"] == {"prompt": PROMPT_TEXT}
    assert "max_images" not in payload["input"]
    assert "image_resolution" not in payload["input"]


@pytest.mark.parametrize(
    "spec",
    [
        ParameterSpec(type=ParameterType.INTEGER, min=1, max=6, required=True),
        ParameterSpec(type=ParameterType.INTEGER, min=1, max=6, required=True, default=1),
        ParameterSpec(type=ParameterType.INTEGER, min=1, max=6, default=2),
        ParameterSpec(type=ParameterType.INTEGER, min=1, max=6, default=1),
        ParameterSpec(type=ParameterType.INTEGER, min=1, max=6),
    ],
    ids=["required", "required-default-1", "default-2", "optional-default-1", "optional"],
)
def test_declared_max_images_always_sends_requested_one(spec: ParameterSpec) -> None:
    """Объявленное поле всегда получает запрошенную 1, а не model/API default."""
    effective = _effective(parameters={"max_images": spec})
    validated = validate_model_request(effective, _request(max_images=1))

    payload = build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    assert payload["input"] == {"prompt": PROMPT_TEXT, "max_images": 1}


def test_multiple_outputs_without_declaration_is_rejected_before_http() -> None:
    """max_images > 1 у модели без объявленной поддержки — ошибка до HTTP."""
    effective = _effective()
    validated = validate_model_request(effective, _request(max_images=3))

    with pytest.raises(UnsupportedCapabilityError):
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)


def test_capability_alone_does_not_authorize_max_images() -> None:
    """Capability результата не доказывает поддержку поля запроса."""
    effective = _effective(capabilities={"multiple_outputs": CapabilityNode(supported=True, max=6)})
    validated = validate_model_request(effective, _request(max_images=3))
    with pytest.raises(UnsupportedCapabilityError):
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    default = validate_model_request(effective, _request())
    assert build_media_request(default, [], max_body_bytes=HUGE_BODY_BYTES)["input"] == {
        "prompt": PROMPT_TEXT
    }


@pytest.mark.parametrize("count", range(1, 7))
def test_declared_max_images_accepts_entire_schema_range(count: int) -> None:
    effective = _effective(parameters={"max_images": ParameterSpec(type=ParameterType.INTEGER)})
    validated = validate_model_request(effective, _request(max_images=count))
    assert (
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)["input"]["max_images"]
        == count
    )


def test_declared_max_images_sent_with_capability() -> None:
    effective = _effective(
        parameters={"max_images": ParameterSpec(type=ParameterType.INTEGER, min=1, max=6)},
        capabilities={"multiple_outputs": CapabilityNode(supported=True, max=6)},
    )
    validated = validate_model_request(effective, _request(max_images=3))
    assert (
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)["input"]["max_images"]
        == 3
    )


@pytest.mark.parametrize("declared", [False, True], ids=["absent", "without-model-max"])
def test_polza_schema_max_images_cap_even_without_model_max(declared: bool) -> None:
    """ImageInputDto допускает до шести независимо от границы модели."""
    parameters = (
        {"max_images": ParameterSpec(type=ParameterType.INTEGER, min=1)} if declared else {}
    )
    effective = _effective(parameters=parameters)
    seven = validate_model_request(effective, _request(max_images=7))
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(seven, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "max_images", "max": 6}

    if declared:
        six = validate_model_request(effective, _request(max_images=6))
        assert (
            build_media_request(six, [], max_body_bytes=HUGE_BODY_BYTES)["input"]["max_images"] == 6
        )


def test_reference_images_keep_order_and_use_actual_mime() -> None:
    """Порядок images сохраняется, MIME берётся из фактических байтов."""
    png = png_bytes()
    webp = webp_lossy_bytes()
    effective = _effective()
    refs = [
        _input_ref("first.png", png, position=0),
        _input_ref("second.webp", webp, position=1),
    ]
    validated = validate_model_request(effective, _request(images=refs))

    payload = build_media_request(validated, [png, webp], max_body_bytes=HUGE_BODY_BYTES)

    images = payload["input"]["images"]
    assert images == [
        {"type": "base64", "data": _data_uri(png)},
        {"type": "base64", "data": _data_uri(webp)},
    ]
    assert images[0]["data"].startswith("data:image/png;base64,")
    assert images[1]["data"].startswith("data:image/webp;base64,")


def test_spoofed_declared_mime_is_not_trusted() -> None:
    """Объявленный MIME не принимается на веру: фактический тип перепроверяется."""
    png = png_bytes()
    ref = _input_ref("spoof.png", png, position=0, mime_type="image/gif")
    validated = validate_model_request(_effective(), _request(images=[ref]))

    with pytest.raises(UnsupportedInputFormatError):
        build_media_request(validated, [png], max_body_bytes=HUGE_BODY_BYTES)


def test_hash_mismatch_is_rejected_before_encoding() -> None:
    """Подменённые байты с тем же размером отклоняются по SHA-256."""
    png = png_bytes()
    ref = _input_ref("ref.png", png, position=0)
    validated = validate_model_request(_effective(), _request(images=[ref]))
    tampered = png[:-1] + bytes([png[-1] ^ 0x01])

    with pytest.raises(InvalidParameterValueError):
        build_media_request(validated, [tampered], max_body_bytes=HUGE_BODY_BYTES)


def test_size_mismatch_is_rejected_before_encoding() -> None:
    """Несовпадение длины байтов с подготовленным size_bytes — ошибка до HTTP."""
    png = png_bytes()
    ref = _input_ref("ref.png", png, position=0)
    validated = validate_model_request(_effective(), _request(images=[ref]))

    with pytest.raises(InvalidParameterValueError):
        build_media_request(validated, [png + b"extra"], max_body_bytes=HUGE_BODY_BYTES)


def test_reference_count_mismatch_is_rejected() -> None:
    """Число переданных блоков должно совпадать с числом reference images."""
    png = png_bytes()
    ref = _input_ref("ref.png", png, position=0)
    validated = validate_model_request(_effective(), _request(images=[ref]))

    with pytest.raises(InvalidParameterValueError):
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)


def test_declared_path_is_not_read_or_trusted() -> None:
    """Путь `InputRef` не открывается: валидные байты кодируются без чтения файла."""
    png = png_bytes()
    ref = _input_ref("definitely/does/not/exist.png", png, position=0)
    validated = validate_model_request(_effective(), _request(images=[ref]))

    payload = build_media_request(validated, [png], max_body_bytes=HUGE_BODY_BYTES)

    assert payload["input"]["images"] == [{"type": "base64", "data": _data_uri(png)}]


def test_unrecognized_image_bytes_are_rejected() -> None:
    """Байты, не распознаваемые как изображение, отклоняются до кодирования."""
    garbage = b"not an image at all"
    ref = _input_ref("noise.png", garbage, position=0, mime_type="image/png")
    validated = validate_model_request(_effective(), _request(images=[ref]))

    with pytest.raises(UnsupportedInputFormatError):
        build_media_request(validated, [garbage], max_body_bytes=HUGE_BODY_BYTES)


def test_explicit_size_cap_rejects_before_http() -> None:
    """Локальный safety-cap отклоняет большой запрос и назван локальным, не Polza-лимитом."""
    png = png_bytes()
    validated = validate_model_request(_effective(), _request())

    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [png], max_body_bytes=4)

    assert excinfo.value.details["max_bytes"] == 4
    assert "не документированный" in excinfo.value.message
    assert "safety" in excinfo.value.message


def test_surrogate_prompt_is_safe_typed_pre_submit_error() -> None:
    """Недопустимый UTF-8 prompt не раскрывается через cause/context или traceback."""
    canary = "secret-prompt-canary\ud800"
    request = _request().model_copy(update={"prompt": CompiledPrompt(text=canary, source_count=1)})
    validated = validate_model_request(_effective(), request)

    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    error = excinfo.value
    assert error.details == {"parameter": "prompt"}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "secret-prompt-canary" not in "".join(traceback.format_exception(error))


def test_surrogate_remote_id_is_safe_typed_pre_submit_error() -> None:
    """Точная JSON-сериализация тоже не раскрывает некодируемый remote ID."""
    canary = "secret-remote-canary\ud800"
    effective = _effective().model_copy(update={"remote_model_id": canary})
    validated = validate_model_request(effective, _request())

    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)

    error = excinfo.value
    assert error.details == {"parameter": "body"}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "secret-remote-canary" not in "".join(traceback.format_exception(error))


def test_positive_size_cap_is_required() -> None:
    """Нулевой или булев safety-cap — ошибка вызывающей стороны."""
    validated = validate_model_request(_effective(), _request())

    with pytest.raises(ValueError):
        build_media_request(validated, [], max_body_bytes=0)
    with pytest.raises(ValueError):
        build_media_request(validated, [], max_body_bytes=True)


def test_exact_serialized_body_size_is_enforced() -> None:
    """Точный размер сериализованного тела проверяется отдельно от нижней оценки."""
    validated = validate_model_request(_effective(), _request())
    payload = build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    body_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # Ровно на границе — принимается; на байт меньше — отклоняется после сериализации.
    build_media_request(validated, [], max_body_bytes=body_bytes)
    with pytest.raises(InvalidParameterValueError):
        build_media_request(validated, [], max_body_bytes=body_bytes - 1)


def test_provider_options_only_documented_declared_scalars() -> None:
    """Допустимы только пересечения Polza ImageInputDto и effective parameters."""
    effective = _effective(
        parameters={
            "guidance_scale": ParameterSpec(type=ParameterType.NUMBER, min=0, max=20),
            "strength": ParameterSpec(type=ParameterType.NUMBER),
            "watermark": ParameterSpec(type=ParameterType.STRING, max_length=8),
            "isEnhance": ParameterSpec(type=ParameterType.BOOLEAN),
            "enable_safety_checker": ParameterSpec(type=ParameterType.BOOLEAN),
            "upscale_factor": ParameterSpec(type=ParameterType.ENUM, values=("2", "4")),
        }
    )
    request = _request(
        provider_options={
            "guidance_scale": 2.5,
            "strength": 1.0,
            "watermark": "brand",
            "isEnhance": False,
            "enable_safety_checker": True,
            "upscale_factor": "2",
        }
    )
    validated = validate_model_request(effective, request)
    assert build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)["input"] == {
        "prompt": PROMPT_TEXT,
        "guidance_scale": 2.5,
        "strength": 1.0,
        "watermark": "brand",
        "isEnhance": False,
        "enable_safety_checker": True,
        "upscale_factor": "2",
    }


def test_unicode_prompt_and_option_are_serialized_without_normalization() -> None:
    effective = _effective(parameters={"watermark": ParameterSpec(type=ParameterType.STRING)})
    prompt = "Звёзды 🪐"
    request = _request(provider_options={"watermark": "水彩 🎨"}).model_copy(
        update={"prompt": CompiledPrompt(text=prompt, source_count=1)}
    )
    validated = validate_model_request(effective, request)
    payload = build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert payload["input"] == {"prompt": prompt, "watermark": "水彩 🎨"}
    byte_count = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    build_media_request(validated, [], max_body_bytes=byte_count)
    with pytest.raises(InvalidParameterValueError):
        build_media_request(validated, [], max_body_bytes=byte_count - 1)


def test_optional_declared_provider_option_none_is_omitted() -> None:
    effective = _effective(parameters={"watermark": ParameterSpec(type=ParameterType.STRING)})
    validated = validate_model_request(effective, _request(provider_options={"watermark": None}))
    assert build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)["input"] == {
        "prompt": PROMPT_TEXT
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("undeclared", "canary-secret"),
        ("canary-secret-key", None),
        ("prompt", None),
        ("callBackUrl", None),
        ("prompt", "canary-secret"),
        ("images", [{"type": "url", "data": "https://canary-secret.invalid"}]),
        ("max_images", 2),
        ("aspect_ratio", "1:1"),
        ("callBackUrl", "https://canary-secret.invalid"),
        ("font_inputs", [{"url": "https://canary-secret.invalid"}]),
        ("super_resolution_references", ["https://canary-secret.invalid"]),
    ],
)
def test_provider_options_reject_unsupported_even_if_declared(key: str, value: object) -> None:
    effective = _effective(parameters={key: ParameterSpec(type=ParameterType.STRING)})
    validated = validate_model_request(effective, _request(provider_options={key: value}))
    with pytest.raises(UnsupportedParameterError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.code == "UNSUPPORTED_PARAMETER"
    assert "canary-secret" not in str(excinfo.value)
    assert "canary-secret" not in str(excinfo.value.details)
    assert key not in str(excinfo.value.details)


@pytest.mark.parametrize(
    "key,spec,value",
    [
        ("guidance_scale", ParameterSpec(type=ParameterType.NUMBER, min=0, max=20), 21),
        ("guidance_scale", ParameterSpec(type=ParameterType.NUMBER), float("nan")),
        ("guidance_scale", ParameterSpec(type=ParameterType.NUMBER), float("inf")),
        ("guidance_scale", ParameterSpec(type=ParameterType.NUMBER), True),
        ("guidance_scale", ParameterSpec(type=ParameterType.STRING), 2.5),
        ("guidance_scale", ParameterSpec(type=ParameterType.NUMBER), {"url": "canary-secret"}),
        ("strength", ParameterSpec(type=ParameterType.NUMBER), -0.1),
        ("strength", ParameterSpec(type=ParameterType.NUMBER), 1.1),
        ("watermark", ParameterSpec(type=ParameterType.STRING, max_length=3), "canary-secret"),
        ("watermark", ParameterSpec(type=ParameterType.STRING), "\ud800"),
        ("upscale_factor", ParameterSpec(type=ParameterType.STRING), "16"),
        ("upscale_factor", ParameterSpec(type=ParameterType.ENUM, values=("2",)), "4"),
        ("isEnhance", ParameterSpec(type=ParameterType.BOOLEAN), 1),
    ],
)
def test_provider_options_reject_invalid_scalar_without_leak(
    key: str, spec: ParameterSpec, value: object
) -> None:
    effective = _effective(parameters={key: spec})
    validated = validate_model_request(effective, _request(provider_options={key: value}))
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert "canary-secret" not in str(excinfo.value)
    assert "canary-secret" not in str(excinfo.value.details)


@pytest.mark.parametrize("default", [None, "safe-brand"], ids=["no-default", "documented"])
def test_required_provider_option_is_explicit_or_fails_safely(default: str | None) -> None:
    """Required provider option не полагается на неявное поведение Polza."""
    effective = _effective(
        parameters={
            "watermark": ParameterSpec(type=ParameterType.STRING, required=True, default=default)
        }
    )
    present = validate_model_request(
        effective, _request(provider_options={"watermark": "provided"})
    )
    assert (
        build_media_request(present, [], max_body_bytes=HUGE_BODY_BYTES)["input"]["watermark"]
        == "provided"
    )

    for options in ({}, {"watermark": None}):
        missing = validate_model_request(effective, _request(provider_options=options))
        if default is not None:
            assert (
                build_media_request(missing, [], max_body_bytes=HUGE_BODY_BYTES)["input"][
                    "watermark"
                ]
                == default
            )
        else:
            with pytest.raises(InvalidParameterValueError) as excinfo:
                build_media_request(missing, [], max_body_bytes=HUGE_BODY_BYTES)
            assert excinfo.value.details == {"parameter": "provider_options", "required": True}
            assert "watermark" not in str(excinfo.value)


@pytest.mark.parametrize(
    "name,provider_field,default",
    [
        ("aspect_ratio", "aspect_ratio", "16:9"),
        ("resolution", "image_resolution", "2K"),
        ("seed", "seed", 0),
        ("quality", "quality", "high"),
        ("output_format", "output_format", "png"),
    ],
)
def test_required_stable_parameter_materializes_documented_default(
    name: str, provider_field: str, default: str | int
) -> None:
    spec = ParameterSpec(
        type=ParameterType.INTEGER if name == "seed" else ParameterType.STRING,
        required=True,
        default=default,
    )
    effective = _effective(parameters={name: spec})
    validated = validate_model_request(effective, _request())
    assert (
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)["input"][provider_field]
        == default
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("aspect_ratio", "1:2"),
        ("resolution", "8K"),
        ("quality", "ultra"),
        ("output_format", "tiff"),
    ],
)
def test_explicit_stable_value_outside_image_input_dto_is_rejected(name: str, value: str) -> None:
    """Даже разрешённое synthetic effective значение ограничено глобальной схемой."""
    effective = _effective(parameters={name: ParameterSpec(type=ParameterType.STRING)})
    validated = validate_model_request(effective, _request(**{name: value}))
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": name}
    assert value not in str(excinfo.value)


@pytest.mark.parametrize(
    "name,value",
    [
        ("aspect_ratio", "1:2"),
        ("resolution", "8K"),
        ("quality", "ultra"),
        ("output_format", "tiff"),
    ],
)
def test_required_stable_default_outside_image_input_dto_is_rejected(name: str, value: str) -> None:
    effective = _effective(
        parameters={name: ParameterSpec(type=ParameterType.STRING, required=True, default=value)}
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": name, "required": True}
    assert value not in str(excinfo.value)


def test_seed_wrong_type_is_rejected_at_schema_boundary() -> None:
    effective = _effective(parameters={"seed": ParameterSpec(type=ParameterType.INTEGER)})
    validated = validate_model_request(effective, _request())
    # Simulate an invalid post-validation mutation at the adapter boundary.
    invalid = validated.request.model_copy(update={"seed": True})
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(
            validated.__class__(request=invalid, effective=effective),
            [],
            max_body_bytes=HUGE_BODY_BYTES,
        )
    assert excinfo.value.details == {"parameter": "seed"}


@pytest.mark.parametrize("name", ["callBackUrl", "font_inputs", "unknown-canary-key"])
@pytest.mark.parametrize("default", [None, "synthetic-default"])
def test_required_unrepresented_parameter_rejected_without_key_leak(
    name: str, default: str | None
) -> None:
    effective = _effective(
        parameters={name: ParameterSpec(type=ParameterType.STRING, required=True, default=default)}
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(UnsupportedParameterError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "model_parameters", "required": True}
    assert name not in str(excinfo.value)
    assert name not in str(excinfo.value.details)


def test_required_images_parameter_needs_reference_content() -> None:
    effective = _effective(
        parameters={"images": ParameterSpec(type=ParameterType.STRING, required=True)}
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "--image", "required": True}

    png = png_bytes()
    with_ref = validate_model_request(
        effective, _request(images=[_input_ref("ref.png", png, position=0)])
    )
    assert build_media_request(with_ref, [png], max_body_bytes=HUGE_BODY_BYTES)["input"][
        "images"
    ] == [{"type": "base64", "data": _data_uri(png)}]


def test_required_provider_option_with_unsupported_schema_default_fails() -> None:
    effective = _effective(
        parameters={
            "upscale_factor": ParameterSpec(
                type=ParameterType.ENUM, values=("16",), required=True, default="16"
            )
        }
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "provider_options", "required": True}
    assert "16" not in str(excinfo.value)


def test_required_stable_parameter_with_unsupported_schema_default_fails() -> None:
    effective = _effective(
        parameters={
            "quality": ParameterSpec(
                type=ParameterType.STRING, required=True, default="secret-custom"
            )
        }
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "quality", "required": True}
    assert "secret-custom" not in str(excinfo.value)


def test_required_stable_parameter_with_unmappable_default_fails_before_http() -> None:
    effective = _effective(
        parameters={"seed": ParameterSpec(type=ParameterType.NUMBER, required=True, default=0.5)}
    )
    validated = validate_model_request(effective, _request())
    with pytest.raises(InvalidParameterValueError) as excinfo:
        build_media_request(validated, [], max_body_bytes=HUGE_BODY_BYTES)
    assert excinfo.value.details == {"parameter": "seed", "required": True}


def test_probe_failure_does_not_chain_raw_png_chunk_name() -> None:
    """Сырые parser details не появляются даже в полном formatted traceback."""
    png = png_bytes()
    # После IHDR — chunk с canary-именем и намеренно неверным CRC.
    broken = png[:33] + b"\x00\x00\x00\x00LEAK\x00\x00\x00\x00" + png[33:]
    with pytest.raises(InvalidImageContentError, match="LEAK"):
        probe_image(broken)
    ref = _input_ref("broken.png", broken, position=0, mime_type="image/png")
    validated = validate_model_request(_effective(), _request(images=[ref]))
    with pytest.raises(UnsupportedInputFormatError) as excinfo:
        build_media_request(validated, [broken], max_body_bytes=HUGE_BODY_BYTES)
    error = excinfo.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "LEAK" not in "".join(traceback.format_exception(error))


def test_request_mapper_imports_no_transport() -> None:
    """Mapper не тянет HTTP/сетевой транспорт: C09a — только чистый mapping."""
    tree = ast.parse(REQUEST_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    roots = {name.split(".")[0] for name in imported}
    assert roots & {"httpx", "requests", "http", "socket", "urllib", "aiohttp"} == set()


def test_fixtures_are_synthetic_not_production_model_ids() -> None:
    """Тестовый binding синтетический; примеры документации не объявлены живыми."""
    effective = _effective()

    assert effective.remote_model_id == SYNTHETIC_REMOTE_ID
    for documented_example in ("seedream-3", "gpt-image-1", "google/gemini-2.5-flash-image"):
        assert effective.remote_model_id != documented_example
