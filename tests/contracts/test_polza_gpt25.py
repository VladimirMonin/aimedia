"""Documented Sunburst/Flare bindings; offline transport, no live claims."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from image_fixtures import jpeg_bytes, png_bytes, webp_lossless_bytes

from aimedia.application.inputs import snapshot_reference_images
from aimedia.domain import CompiledPrompt, ImageGenerationRequest, ModelRef, ProviderRef
from aimedia.domain.errors import DomainError
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.registry import ModelResolver
from aimedia.registry.builtin import load_builtin_registry

MODELS = ["gpt-image-2-5-sunburst", "gpt-image-2-5-flare"]
RATIOS = [
    "auto",
    "1:1",
    "3:2",
    "2:3",
    "4:3",
    "3:4",
    "16:9",
    "9:16",
    "21:9",
    "27:16",
    "16:27",
    "9:8",
    "8:9",
]
PAIRS = [
    (r, a)
    for r in ("1K", "2K", "4K")
    for a in RATIOS
    if not (a == "auto" and r != "1K") and not (a == "1:1" and r == "4K")
]


def submit(model, *, prompt="synthetic robot", **options):
    req = ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id=model),
        prompt=CompiledPrompt(text=prompt, source_count=1),
        **options,
    )
    calls = []

    async def attempt():
        def respond(sent):
            calls.append(json.loads(sent.content))
            return httpx.Response(
                200,
                json={
                    "id": "synthetic-gpt25",
                    "object": "media.generation",
                    "status": "pending",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = PolzaProviderGateway(
                client=client,
                api_key="synthetic_not_a_real_key",
                effective=ModelResolver(load_builtin_registry()).resolve(model, "polza"),
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


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("resolution,ratio", PAIRS)
def test_documented_pairs_reach_exact_mie_once(model, resolution, ratio):
    calls, error = submit(model, resolution=resolution, aspect_ratio=ratio)
    assert error is None and len(calls) == 1
    assert calls[0] == {
        "model": "openai/" + model.replace("2-5", "2.5"),
        "provider": {"only": ["mie"], "allow_fallbacks": False, "max_price": {"image": 11}},
        "async": True,
        "input": {
            "prompt": "synthetic robot",
            "image_resolution": resolution,
            "aspect_ratio": ratio,
            "max_images": 1,
        },
    }


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    "options",
    [
        {"resolution": "2K"},
        {"resolution": "4K", "aspect_ratio": "auto"},
        {"resolution": "4K", "aspect_ratio": "1:1"},
        {"max_images": 2},
        {"quality": "high"},
        {"seed": 1},
        {"aspect_ratio": "1:4"},
    ],
)
def test_unsupported_combinations_refuse_before_http(model, options):
    calls, error = submit(model, **options)
    assert isinstance(error, DomainError) and not calls


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("length", [20000, 20001])
def test_prompt_limit_from_public_catalog(model, length):
    calls, error = submit(model, prompt="x" * length)
    assert len(calls) == int(length == 20000)
    assert (error is None) == (length == 20000)
    if error:
        assert error.details["max_chars"] == 20000
        assert "x" * length not in str(error)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("count", [16, 17])
def test_multiple_reference_boundary_and_encoded_bytes(tmp_path, model, count):
    contents = [png_bytes(), jpeg_bytes(), webp_lossless_bytes()]
    paths = []
    for i in range(count):
        path = tmp_path / f"ref-{i}.img"
        path.write_bytes(contents[i % 3])
        paths.append(path)
    snapshots = snapshot_reference_images(paths)
    calls, error = submit(model, images=[s.ref for s in snapshots])
    if count == 17:
        assert error.code.value == "TOO_MANY_REFERENCE_IMAGES" and not calls
        return
    assert error is None and len(calls) == 1
    images = calls[0]["input"]["images"]
    assert len(images) == count
    for image, snapshot in zip(images, snapshots, strict=True):
        header, data = image["data"].split(",", 1)
        assert header == f"data:{snapshot.ref.mime_type};base64"
        assert base64.b64decode(data) == snapshot.content
