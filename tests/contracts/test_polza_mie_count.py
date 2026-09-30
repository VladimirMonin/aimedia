"""Documented MIE count route only; no live calls or catalog inference."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from aimedia.domain import CompiledPrompt, ImageGenerationRequest, ModelRef, ProviderRef
from aimedia.domain.errors import InvalidParameterValueError
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.providers.polza.media import build_media_request
from aimedia.registry import ModelResolver, validate_model_request
from aimedia.registry.builtin import load_builtin_registry

MODEL = "gpt-5-4-image-2-mie"
REMOTE = "openai/gpt-5.4-image-2@mie"


def request(**options):
    return ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id=MODEL),
        prompt=CompiledPrompt(text="synthetic robot", source_count=1),
        **options,
    )


def effective():
    return ModelResolver(load_builtin_registry()).resolve(MODEL, "polza")


@pytest.mark.parametrize("count", [1, 2, 3, 4])
@pytest.mark.parametrize("ratio", ["auto", "1:1", "9:16", "16:9", "4:3", "3:4"])
def test_mie_mapper_exact_n_with_conservative_defaults(count, ratio):
    validated = validate_model_request(effective(), request(max_images=count, aspect_ratio=ratio))
    assert build_media_request(validated, [], max_body_bytes=4096) == {
        "model": REMOTE,
        "input": {
            "prompt": "synthetic robot",
            "image_resolution": "1K",
            "aspect_ratio": ratio,
            "n": count,
        },
    }


@pytest.mark.parametrize(
    "remote",
    [
        "openai/gpt-5.4-image-2@openai",
        "openai/gpt-5.4-image-2@MIE",
        "openai/gpt-5.4-image-2@mie@mie",
        "openai/gpt-5.4-image-2@mie/extra",
        "openai/gpt-5.4-image-2@mie?token=synthetic",
        "openai/gpt-5.4-image-2@міе",
        "https://polza.ai/api/v1/media",
        "x" * 129,
        "model\n@mie",
    ],
)
def test_invalid_remote_identity_or_unconfirmed_qualifier_never_http(remote):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        raise AssertionError("HTTP must not occur")

    definition = effective().model_copy(update={"remote_model_id": remote})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = PolzaProviderGateway(
                client=client,
                api_key="synthetic_not_a_real_key",
                effective=definition,
                max_body_bytes=4096,
                max_response_bytes=4096,
            )
            with pytest.raises(InvalidParameterValueError):
                await gateway.submit(request(max_images=2))

    asyncio.run(run())
    assert not calls


@pytest.mark.parametrize("resolution", ["2K", "4K"])
def test_mie_higher_documented_resolutions_not_in_enabled_slice(resolution):
    with pytest.raises(InvalidParameterValueError):
        validate_model_request(effective(), request(resolution=resolution))
