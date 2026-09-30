"""Fixed documented MIE selection/price filter; offline, never billing evidence."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from aimedia.domain import CompiledPrompt, ImageGenerationRequest, ModelRef, ProviderRef
from aimedia.domain.errors import InvalidParameterValueError, UnsupportedParameterError
from aimedia.providers.polza.gateway import POLZA_API_BASE_URL, PolzaProviderGateway
from aimedia.providers.polza.media import build_media_request, serialize_media_request
from aimedia.registry import ModelResolver, validate_model_request
from aimedia.registry.builtin import load_builtin_registry
from aimedia.registry.models import CatalogPricing

MODELS = [
    ("qwen-image-2-1", "qwen/image-2.1", 6),
    ("gemini-3-1-flash-image-preview", "google/gemini-3.1-flash-image-preview", 11),
    ("gpt-5-4-image-2-mie", "openai/gpt-5.4-image-2@mie", 4),
]


def definition(model):
    return ModelResolver(load_builtin_registry()).resolve(model, "polza")


def request(model, **options):
    return ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id=model),
        prompt=CompiledPrompt(text="synthetic robot", source_count=1),
        **options,
    )


def payload(effective, req, cap=4096):
    return build_media_request(validate_model_request(effective, req), [], max_body_bytes=cap)


def submit(effective, req, calls, cap=4096):
    def handler(sent):
        calls.append(sent)
        return httpx.Response(
            200, json={"id": "synthetic-job", "object": "media.generation", "status": "pending"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = PolzaProviderGateway(
                client=client,
                api_key="synthetic_not_a_real_key",
                effective=effective,
                max_body_bytes=cap,
                max_response_bytes=4096,
            )
            return await gateway.submit(req)

    return asyncio.run(run())


@pytest.mark.parametrize("model,remote,ceiling", MODELS)
def test_builtin_exact_selected_mie_body_and_one_serialized_post(model, remote, ceiling):
    effective = definition(model)
    req = request(model)
    expected_input = {"prompt": "synthetic robot"}
    if model == "gpt-5-4-image-2-mie":
        expected_input.update(aspect_ratio="auto", image_resolution="1K", n=1)
    expected = {
        "model": remote,
        "input": expected_input,
        "provider": {"only": ["mie"], "allow_fallbacks": False, "max_price": {"image": ceiling}},
    }
    assert payload(effective, req) == expected
    calls = []
    submit(effective, req, calls)
    assert len(calls) == 1
    sent = calls[0]
    assert sent.method == "POST"
    assert str(sent.url) == f"{POLZA_API_BASE_URL}/media"
    assert sent.content == serialize_media_request(expected)
    decoded = json.loads(sent.content)
    assert type(decoded["provider"]["max_price"]["image"]) is int
    assert decoded["provider"]["allow_fallbacks"] is False


@pytest.mark.parametrize("model,remote,ceiling", MODELS)
def test_exact_cap_includes_additional_routing_bytes(model, remote, ceiling):
    effective = definition(model)
    req = request(model)
    built = payload(effective, req)
    exact = len(serialize_media_request(built))
    without_routing = len(
        serialize_media_request({k: v for k, v in built.items() if k != "provider"})
    )
    assert exact > without_routing
    calls = []
    submit(effective, req, calls, exact)
    assert len(calls) == 1
    for cap in (exact - 1, without_routing):
        calls = []
        with pytest.raises(InvalidParameterValueError):
            submit(effective, req, calls, cap)
        assert calls == []


@pytest.mark.parametrize("model,remote,ceiling", MODELS)
@pytest.mark.parametrize(
    "pricing",
    [
        None,
        CatalogPricing(currency="USD", by_resolution={"1K": Decimal("1")}),
        CatalogPricing.model_construct(currency="RUB", by_resolution={}),
        CatalogPricing.model_construct(currency="RUB", by_resolution={"1K": Decimal("NaN")}),
        CatalogPricing.model_construct(currency="RUB", by_resolution={"1K": Decimal("Infinity")}),
        CatalogPricing.model_construct(currency="RUB", by_resolution={"1K": Decimal("-1")}),
        CatalogPricing.model_construct(currency="RUB", by_resolution={"1K": 1.5}),
    ],
)
def test_known_binding_unusable_pricing_safe_typed_refusal_zero_http(
    model, remote, ceiling, pricing
):
    effective = definition(model).model_copy(update={"pricing": pricing})
    calls = []
    with pytest.raises(InvalidParameterValueError) as caught:
        submit(effective, request(model), calls)
    assert calls == []
    assert caught.value.details == {"parameter": "pricing"}
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "synthetic robot" not in str(caught.value)


@pytest.mark.parametrize("model,remote,ceiling", MODELS)
@pytest.mark.parametrize("amount,expected", [("0", 0), ("6", 6), ("6.000000000000000001", 7)])
def test_ceiling_uses_effective_exact_decimal_published_max(
    model, remote, ceiling, amount, expected
):
    effective = definition(model).model_copy(
        update={
            "pricing": CatalogPricing(
                currency="RUB", by_resolution={"1K": Decimal("0"), "2K": Decimal(amount)}
            )
        }
    )
    assert payload(effective, request(model))["provider"]["max_price"] == {"image": expected}


@pytest.mark.parametrize(
    "remote",
    ["synthetic/image", "qwen/image-2.1-other", "google/gemini-3.1-flash-image-preview-other"],
)
def test_unknown_and_synthetic_mapping_unchanged_without_pricing(remote):
    model = "qwen-image-2-1"
    effective = definition(model).model_copy(update={"remote_model_id": remote, "pricing": None})
    assert payload(effective, request(model)) == {
        "model": remote,
        "input": {"prompt": "synthetic robot"},
    }


@pytest.mark.parametrize("model,remote,ceiling", MODELS)
@pytest.mark.parametrize(
    "options",
    [
        {"provider": {"only": ["other"], "allow_fallbacks": True}},
        {"only": ["other"]},
        {"allow_fallbacks": True},
        {"max_price": {"image": 999}},
    ],
)
def test_untrusted_caller_options_cannot_override_fixed_routing(model, remote, ceiling, options):
    calls = []
    with pytest.raises(UnsupportedParameterError):
        submit(definition(model), request(model, provider_options=options), calls)
    assert calls == []
