"""Finite documented HTTP diagnostics, never provider text or secret tokens."""

import asyncio
import json
import traceback

import httpx
import pytest
from test_polza_gateway import API_KEY, _gateway, _media_ref, _request

from aimedia.application.single_image import _safe_error
from aimedia.domain import JobError, ProviderError

CODES = [
    "BAD_REQUEST",
    "UNAUTHORIZED",
    "api_key_revoked",
    "INSUFFICIENT_BALANCE",
    "FORBIDDEN",
    "NOT_FOUND",
    "REQUEST_TIMEOUT",
    "CONFLICT",
    "PAYLOAD_TOO_LARGE",
    "TOO_MANY_REQUESTS",
    "BAD_GATEWAY",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
]
REASON = "noProvidersForModel"


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize(
    "method,status", [("POST", 400), ("POST", 408), ("POST", 503), ("GET", 503)]
)
def test_documented_diagnostics_do_not_change_http_classification(code, method, status):
    async def attempt():
        calls = []

        def respond(request):
            calls.append(request.method)
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": code,
                        "message": API_KEY,
                        "metadata": {"reason": REASON, "raw": API_KEY, "provider_name": API_KEY},
                        "details": {"body": API_KEY},
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = _gateway(client)
            with pytest.raises(ProviderError) as caught:
                await (
                    gateway.submit(_request())
                    if method == "POST"
                    else gateway.get_status(_media_ref())
                )
        error = caught.value.error
        assert error.provider_code == code
        persisted = _safe_error(error, "Provider failed")
        assert persisted.provider_code == code
        assert persisted.details == {"http_status": status, "reason": REASON}
        assert persisted.retryable is error.retryable
        assert error.details == {
            "http_status": status,
            "reason": REASON,
            **({"operation": "submit"} if method == "POST" and status != 400 else {}),
        }
        assert error.code == (
            "SUBMIT_UNCERTAIN"
            if method == "POST" and status != 400
            else "PROVIDER_HTTP_ERROR"
            if status == 400
            else "PROVIDER_UNAVAILABLE"
        )
        assert error.retryable is (True if method == "GET" else None)
        assert API_KEY not in error.model_dump_json()
        assert calls == [method]
        assert caught.value.__cause__ is None and caught.value.__context__ is None

    asyncio.run(attempt())


@pytest.mark.parametrize(
    "candidate",
    [
        API_KEY,
        "BAD_REQUEST_" + API_KEY,
        "https://x.invalid/?key=" + API_KEY,
        "x" * 10000,
        7,
        True,
        None,
        {},
        [],
    ],
)
def test_unknown_or_malformed_new_fields_are_dropped(candidate):
    async def attempt():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": candidate,
                            "message": API_KEY,
                            "trace_id": API_KEY,
                            "metadata": {"reason": candidate, "raw": API_KEY},
                        },
                        "headers": {"Authorization": API_KEY},
                        "url": API_KEY,
                    },
                )
            )
        ) as client:
            with pytest.raises(ProviderError) as caught:
                await _gateway(client).submit(_request())
        assert caught.value.error.provider_code is None
        assert caught.value.error.details == {"http_status": 400}
        assert API_KEY not in caught.value.error.model_dump_json()
        assert API_KEY not in "".join(traceback.format_exception(caught.value))

    asyncio.run(attempt())


@pytest.mark.parametrize("key", ["BAD_REQUEST", "BAD", REASON, "noProviders"])
def test_known_tokens_containing_injected_secret_are_dropped(key):
    async def attempt():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "BAD_REQUEST",
                            "metadata": {"reason": REASON},
                            "trace_id": key,
                        }
                    },
                )
            )
        ) as client:
            gateway = _gateway(client)
            gateway._api_key = key
            with pytest.raises(ProviderError) as caught:
                await gateway.submit(_request())
        assert key not in caught.value.error.model_dump_json()
        assert "trace_id" not in caught.value.error.details

    asyncio.run(attempt())


@pytest.mark.parametrize(
    "body",
    [
        b'{"error":',
        b"[]",
        b'{"error":{"code":"BAD_REQUEST","code":"UNAUTHORIZED","metadata":{"reason":"noProvidersForModel"}}}',
        json.dumps({"error": {"code": "BAD_REQUEST", "metadata": "bad"}}).encode(),
    ],
)
def test_malformed_or_duplicate_error_body_keeps_status_and_closes(body):
    async def attempt():
        responses = []

        def respond(_):
            response = httpx.Response(400, content=body)
            responses.append(response)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ProviderError) as caught:
                await _gateway(client).submit(_request())
        assert caught.value.error.code == "PROVIDER_HTTP_ERROR"
        assert caught.value.error.details == {"http_status": 400}
        assert caught.value.error.provider_code == (
            "BAD_REQUEST" if b'"metadata": "bad"' in body else None
        )
        assert len(responses) == 1 and responses[0].is_closed

    asyncio.run(attempt())


@pytest.mark.parametrize(
    "candidate",
    [
        API_KEY,
        "BAD_REQUEST_" + API_KEY,
        "https://x.invalid/?key=" + API_KEY,
        "x" * 10000,
        "UNKNOWN_CODE",
        "",
        None,
    ],
)
def test_application_rejects_unknown_tokens_even_when_adapter_port_supplies_them(candidate):
    safe = _safe_error(
        JobError(
            code="PROVIDER_HTTP_ERROR",
            message=API_KEY,
            provider_code=candidate,
            provider_message=API_KEY,
            details={
                "http_status": 400,
                "reason": candidate,
                "trace_id": API_KEY,
                "headers": API_KEY,
            },
        ),
        "Provider failed",
    )
    assert safe.provider_code is None
    assert safe.details == {"http_status": 400}
    assert API_KEY not in safe.model_dump_json()


@pytest.mark.parametrize("method", ["POST", "GET"])
@pytest.mark.parametrize("failure", ["encoding", "oversized"])
def test_unreadable_error_body_keeps_existing_classification_and_closes(method, failure):
    async def attempt():
        responses = []

        def respond(_):
            response = httpx.Response(
                400,
                content=json.dumps(
                    {
                        "error": {
                            "code": "BAD_REQUEST",
                            "metadata": {"reason": REASON},
                            "raw": API_KEY,
                        }
                    }
                ).encode(),
                headers={"Content-Encoding": "custom"} if failure == "encoding" else {},
            )
            responses.append(response)
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = _gateway(client, max_response_bytes=8 if failure == "oversized" else 8192)
            with pytest.raises(ProviderError) as caught:
                await (
                    gateway.submit(_request())
                    if method == "POST"
                    else gateway.get_status(_media_ref())
                )
        error = caught.value.error
        assert error.code == (
            "SUBMIT_UNCERTAIN"
            if method == "POST"
            else "PROVIDER_INVALID_RESPONSE"
            if failure == "encoding"
            else "PROVIDER_RESPONSE_TOO_LARGE"
        )
        assert error.provider_code is None and "http_status" not in error.details
        assert API_KEY not in error.model_dump_json()
        assert len(responses) == 1 and responses[0].is_closed

    asyncio.run(attempt())
