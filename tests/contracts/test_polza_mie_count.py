"""CN-05 GPT MIE: single output, all documented settings and multiple inputs."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from image_fixtures import jpeg_bytes, png_bytes, webp_lossless_bytes

from aimedia.application.inputs import snapshot_reference_images
from aimedia.domain import CompiledPrompt, ImageGenerationRequest, ModelRef, ProviderRef
from aimedia.domain.errors import DomainError, InvalidParameterValueError
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.providers.polza.media import build_media_request, serialize_media_request
from aimedia.registry import ModelResolver, validate_model_request
from aimedia.registry.builtin import load_builtin_registry

MODEL = "gpt-5-4-image-2-mie"
REMOTE = "openai/gpt-5.4-image-2"
RATIOS = ["auto", "1:1", "9:16", "16:9", "4:3", "3:4"]
VALID_PAIRS = [
    (r, a)
    for r in ("1K", "2K", "4K")
    for a in RATIOS
    if not (a == "auto" and r != "1K") and not (a == "1:1" and r == "4K")
]


def request(**options):
    return ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id=MODEL),
        prompt=CompiledPrompt(text="synthetic robot", source_count=1),
        **options,
    )


def effective():
    return ModelResolver(load_builtin_registry()).resolve(MODEL, "polza")


def submit(req, definition=None):
    calls = []

    async def attempt():
        def respond(sent):
            calls.append(sent)
            return httpx.Response(
                200, json={"id": "synthetic-job", "object": "media.generation", "status": "pending"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = PolzaProviderGateway(
                client=client,
                api_key="synthetic_not_a_real_key",
                effective=definition or effective(),
                max_body_bytes=1024 * 1024,
                max_response_bytes=4096,
            )
            try:
                await gateway.submit(req)
            except DomainError as exc:
                return exc
        return None

    error = asyncio.run(attempt())
    return calls, error


@pytest.mark.parametrize("resolution,ratio", VALID_PAIRS)
def test_mie_all_fifteen_pairs_single_serialized_post_fixed_price_max(resolution, ratio):
    req = request(resolution=resolution, aspect_ratio=ratio)
    payload = build_media_request(validate_model_request(effective(), req), [], max_body_bytes=4096)
    expected = {
        "model": REMOTE,
        "provider": {"only": ["mie"], "allow_fallbacks": False, "max_price": {"image": 11}},
        "async": True,
        "input": {
            "prompt": "synthetic robot",
            "image_resolution": resolution,
            "aspect_ratio": ratio,
            "max_images": 1,
        },
    }
    assert payload == expected
    calls, error = submit(req)
    assert error is None and len(calls) == 1
    assert calls[0].content == serialize_media_request(payload)
    assert "n" not in json.loads(calls[0].content)["input"]


@pytest.mark.parametrize(
    "options",
    [
        {"resolution": "2K"},
        {"resolution": "4K"},
        {"resolution": "2K", "aspect_ratio": "auto"},
        {"resolution": "4K", "aspect_ratio": "auto"},
        {"resolution": "4K", "aspect_ratio": "1:1"},
        {"resolution": "8K"},
        {"aspect_ratio": "2:3"},
        {"aspect_ratio": "3:2"},
        {"max_images": 2},
        {"max_images": 4},
        {"max_images": 6},
        {"quality": "high"},
        {"seed": 1},
        {"provider_options": {"n": 1}},
        {"provider_options": {"quality": "high"}},
    ],
)
def test_unsupported_pairs_controls_or_output_count_refused_before_http(options):
    calls, error = submit(request(**options))
    assert isinstance(error, DomainError) and not calls


@pytest.mark.parametrize(
    "remote",
    [
        REMOTE + "@mie",
        REMOTE + "@openai",
        REMOTE + "@MIE",
        REMOTE + "@mie@mie",
        REMOTE + "@mie/extra",
        REMOTE + "@mie?token=synthetic",
        REMOTE + "@міе",
        "https://polza.ai/api/v1/media",
        "x" * 129,
        "model\n@mie",
    ],
)
def test_invalid_remote_identity_or_unconfirmed_qualifier_never_http(remote):
    calls, error = submit(request(), effective().model_copy(update={"remote_model_id": remote}))
    assert isinstance(error, InvalidParameterValueError) and not calls


@pytest.mark.parametrize("size,accepted", [(5000, True), (5001, False)])
def test_prompt_documented_limit_is_pre_http_without_text_leak(size, accepted):
    text = "x" * size
    req = request().model_copy(update={"prompt": CompiledPrompt(text=text, source_count=1)})
    calls, error = submit(req)
    assert (error is None) is accepted
    assert len(calls) == int(accepted)
    if error:
        assert error.details == {"parameter": "prompt", "length": size, "max_chars": 5000}
        assert text not in str(error)


@pytest.mark.parametrize("count", [2, 16, 17])
def test_reference_bound_and_actual_png_jpeg_webp_encoding_before_single_post(tmp_path, count):
    contents = [png_bytes(), jpeg_bytes(), webp_lossless_bytes()]
    paths = []
    for i in range(count):
        path = tmp_path / f"ref{i}.img"
        path.write_bytes(contents[i % 3])
        paths.append(path)
    refs = snapshot_reference_images(paths)
    calls, error = submit(
        request(images=[s.ref for s in refs], resolution="2K", aspect_ratio="16:9")
    )
    if count == 17:
        assert error.code.value == "TOO_MANY_REFERENCE_IMAGES" and not calls
        return
    assert error is None and len(calls) == 1
    images = json.loads(calls[0].content)["input"]["images"]
    assert len(images) == count
    for image, snapshot in zip(images, refs, strict=True):
        assert image["type"] == "base64"
        header, encoded = image["data"].split(",", 1)
        assert header == f"data:{snapshot.ref.mime_type};base64"
        assert base64.b64decode(encoded) == snapshot.content
