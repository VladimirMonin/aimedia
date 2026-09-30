"""Контрактные offline-тесты HTTP-шлюза Polza Media (E06, C09c1).

Проверяется транспорт `aimedia.providers.polza.gateway.PolzaProviderGateway`:
точное тело и заголовки `POST /v1/media`, одноразовость submit, переиспользование
инжектированного клиента, безопасность `GET /v1/media/{id}`, отказ от редиректов,
нормализация HTTP-ошибок и точный `Decimal`. Реальный API не вызывается: все
запросы проходят через `httpx.MockTransport`, а offline-guard из `tests/conftest.py`
блокирует любой внешний сокет. Артефакты здесь не скачиваются (C09c2).

Все model/remote ID синтетические; примеры документации не объявляются живыми.
"""

from __future__ import annotations

import ast
import asyncio
import gzip
import hashlib
import json
import traceback
from collections.abc import AsyncIterator, Callable, Coroutine
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from image_fixtures import jpeg_bytes, png_bytes

from aimedia.application.inputs.image_probe import probe_image
from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    InputFileNotFoundError,
    InputKind,
    InputRef,
    InvalidParameterValueError,
    ModelRef,
    PollingProviderGateway,
    ProviderError,
    ProviderGateway,
    ProviderJobState,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    UnknownModelError,
    UnknownProviderError,
)
from aimedia.providers.polza.gateway import (
    POLZA_API_BASE_URL,
    SUBMIT_UNCERTAIN,
    PolzaProviderGateway,
)
from aimedia.providers.polza.media import (
    build_media_request,
    serialize_media_request,
)
from aimedia.registry import (
    ModelResolver,
    ModelStatus,
    ParameterSpec,
    ProviderBinding,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord

MODEL_ID = "synthetic-image"
PROVIDER_ID = "polza"
SYNTHETIC_REMOTE_MODEL_ID = "synthetic/remote"
PROMPT_TEXT = "synthetic prompt"
API_KEY = "test-api-key-not-a-real-secret"
REMOTE_ID = "aig_synthetic"
IMAGE_URL = "https://cdn.example.invalid/aig_synthetic.jpg"
CANARY = "secret-provider-canary"

HUGE_BODY_BYTES = 64 * 1024 * 1024
HUGE_RESPONSE_BYTES = 8 * 1024 * 1024

GATEWAY_MODULE = (
    Path(__file__).resolve().parents[2] / "src" / "aimedia" / "providers" / "polza" / "gateway.py"
)


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


class Recorder:
    """Transport handler, сохраняющий запросы и отдающий управляемый ответ."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


@pytest.fixture
def make_client() -> Any:
    """Фабрика инжектируемого `AsyncClient` на `MockTransport` с закрытием в конце."""
    created: list[httpx.AsyncClient] = []

    def factory(
        responder: Callable[[httpx.Request], httpx.Response],
        *,
        follow_redirects: bool = False,
    ) -> tuple[httpx.AsyncClient, Recorder]:
        recorder = Recorder(responder)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(recorder.handler),
            follow_redirects=follow_redirects,
        )
        created.append(client)
        return client, recorder

    yield factory
    for client in created:
        run(client.aclose())


def _effective(
    *,
    parameters: dict[str, ParameterSpec] | None = None,
) -> Any:
    record = ModelRecord(
        schema_version=1,
        model_id=MODEL_ID,
        name="Synthetic image",
        family="image",
        status=ModelStatus.ACTIVE,
        capabilities={},
        inputs={},
        outputs={},
        parameters=parameters or {},
        providers={PROVIDER_ID: ProviderBinding(remote_model_id=SYNTHETIC_REMOTE_MODEL_ID)},
    )
    return ModelResolver([record]).resolve(MODEL_ID, PROVIDER_ID)


def _request(
    *,
    images: list[InputRef] | None = None,
    model_id: str = MODEL_ID,
) -> ImageGenerationRequest:
    return ImageGenerationRequest(
        provider=ProviderRef(id=PROVIDER_ID),
        model=ModelRef(id=model_id),
        prompt=CompiledPrompt(text=PROMPT_TEXT, source_count=1),
        images=images if images is not None else [],
    )


def _input_ref(path: Path, content: bytes, *, position: int) -> InputRef:
    path.write_bytes(content)
    probe = probe_image(content)
    return InputRef(
        kind=InputKind.IMAGE,
        path=path,
        position=position,
        mime_type=probe.mime_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _gateway(
    client: httpx.AsyncClient,
    *,
    effective: Any | None = None,
    max_body_bytes: int = HUGE_BODY_BYTES,
    max_response_bytes: int = HUGE_RESPONSE_BYTES,
) -> PolzaProviderGateway:
    return PolzaProviderGateway(
        client=client,
        api_key=API_KEY,
        effective=effective if effective is not None else _effective(),
        max_body_bytes=max_body_bytes,
        max_response_bytes=max_response_bytes,
    )


def _media_ref(remote_job_id: str = REMOTE_ID) -> RemoteJobRef:
    return RemoteJobRef(
        provider_id=PROVIDER_ID,
        remote_job_id=remote_job_id,
        operation=RemoteOperation.MEDIA,
    )


def _response(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode("utf-8"))


def _pending_body() -> dict[str, Any]:
    return {
        "id": REMOTE_ID,
        "object": "media.generation",
        "status": "pending",
        "created": 1703001244,
        "model": "synthetic/model",
    }


def _completed_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": REMOTE_ID,
        "object": "media.generation",
        "status": "completed",
        "created": 1703001244,
        "model": "synthetic/model",
        "data": {"url": IMAGE_URL},
        "usage": {"output_units": 1, "cost_rub": 0.0831},
    }
    body.update(overrides)
    return body


# --- Порты и инварианты конструкции -----------------------------------------


def test_gateway_satisfies_provider_and_polling_ports(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    assert isinstance(gateway, ProviderGateway)
    assert isinstance(gateway, PollingProviderGateway)
    assert gateway.provider_id == PROVIDER_ID
    assert gateway.capabilities.async_jobs is True
    assert gateway.capabilities.polling is True
    assert gateway.capabilities.cancellation is False


def test_gateway_reuses_injected_client(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    assert gateway.client is client


def test_gateway_source_never_constructs_async_client() -> None:
    """Клиент инжектируется: шлюз не создаёт новый client на вызов/poll."""
    tree = ast.parse(GATEWAY_MODULE.read_text(encoding="utf-8"))
    constructions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            construction = f"{ast.unparse(node.func)}"
            if construction in ("httpx.AsyncClient", "httpx.Client", "httpx.AsyncHTTPTransport"):
                constructions.append(construction)
    assert constructions == []


def test_gateway_uses_shared_serializer_not_duplicate_json_dumps() -> None:
    """Байты тела строятся общим `serialize_media_request`, а не вторым `json.dumps`."""
    tree = ast.parse(GATEWAY_MODULE.read_text(encoding="utf-8"))
    dumps_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if ast.unparse(node.func) == "json.dumps":
                dumps_calls += 1
    assert dumps_calls == 0


def test_api_key_absent_from_repr_str_and_traceback(make_client: Any) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("cannot reach provider")

    client, recorder = make_client(failing)
    gateway = _gateway(client)
    assert API_KEY not in repr(gateway)
    assert API_KEY not in str(gateway)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))
    formatted = "".join(traceback.format_exception(excinfo.value))
    assert API_KEY not in formatted
    assert recorder.requests


# --- Точное тело и auth POST -------------------------------------------------


def test_submit_posts_exact_serialized_body_and_bearer(make_client: Any, tmp_path: Path) -> None:
    png = png_bytes()
    effective = _effective()
    ref_path = tmp_path / "ref.png"
    request = _request(images=[_input_ref(ref_path, png, position=0)])
    assert ref_path.read_bytes() == png

    def responder(req: httpx.Request) -> httpx.Response:
        return _response(200, _pending_body())

    client, recorder = make_client(responder)
    gateway = _gateway(client, effective=effective)
    result = run(gateway.submit(request))

    assert result.state is ProviderJobState.SUBMITTED
    assert len(recorder.requests) == 1
    sent = recorder.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == f"{POLZA_API_BASE_URL}/media"
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent.headers["content-type"] == "application/json"

    expected_payload = build_media_request(
        validate_model_request(effective, request),
        [png],
        max_body_bytes=HUGE_BODY_BYTES,
    )
    expected_bytes = serialize_media_request(expected_payload)
    assert sent.content == expected_bytes
    assert json.loads(sent.content) == expected_payload


def test_submit_completed_synchronous_returns_result(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _completed_body()))
    gateway = _gateway(client)
    result = run(gateway.submit(_request()))
    assert result.state is ProviderJobState.COMPLETED
    assert result.result is not None
    assert result.result.remote_artifacts[0].url == IMAGE_URL


def test_submit_rejects_wrong_model_before_http(make_client: Any) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    with pytest.raises(UnknownModelError):
        run(gateway.submit(_request(model_id="other-model")))
    assert recorder.requests == []


def test_submit_wrong_effective_provider_rejected_without_http(make_client: Any) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    effective = _effective().model_copy(update={"provider_id": "not-polza"})
    gateway = _gateway(client, effective=effective)
    with pytest.raises(UnknownProviderError) as excinfo:
        run(gateway.submit(_request()))
    assert excinfo.value.code == "UNKNOWN_PROVIDER"
    assert recorder.requests == []


# --- Submit: неизвестный исход без повторного POST ---------------------------


def test_submit_transport_timeout_is_uncertain_and_posts_once(make_client: Any) -> None:
    signed = f"https://s3.example.invalid/file.png?X-Amz-Signature={CANARY}"

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"connection failed: {signed}")

    client, recorder = make_client(failing)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert len(recorder.requests) == 1  # нет автоматического повторного POST
    assert error.__cause__ is None
    assert error.__context__ is None
    formatted = "".join(traceback.format_exception(error))
    assert CANARY not in formatted
    assert "s3.example.invalid" not in formatted


def test_submit_disconnect_is_uncertain_and_posts_once(make_client: Any) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset mid-response")

    client, recorder = make_client(failing)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))
    assert excinfo.value.error.code == SUBMIT_UNCERTAIN
    assert len(recorder.requests) == 1


def test_submit_malformed_200_json_is_uncertain_without_body_leak(make_client: Any) -> None:
    """2xx с неразбираемым телом: submit мог быть принят, второй POST запрещён."""

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=f"not json {CANARY}".encode())

    client, recorder = make_client(responder)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert error.error.details == {"operation": "submit"}
    assert len(recorder.requests) == 1  # нет автоматического повторного POST
    assert error.__cause__ is None
    assert error.__context__ is None
    formatted = "".join(traceback.format_exception(error))
    assert CANARY not in formatted
    assert CANARY not in str(error.error.model_dump())


@pytest.mark.parametrize(
    "body",
    [
        {"object": "media.generation", "status": "pending", "created": 1703001244},
        {"id": "../escape", "object": "media.generation", "status": "pending"},
        {"id": REMOTE_ID, "object": "other", "status": "pending"},
        {"id": REMOTE_ID, "object": "media.generation", "status": "bogus"},
        {"id": REMOTE_ID, "object": "media.generation", "status": "completed"},
    ],
    ids=["missing-id", "unsafe-id", "invalid-object", "invalid-status", "completed-no-image"],
)
def test_submit_200_without_usable_envelope_is_uncertain(
    make_client: Any, body: dict[str, Any]
) -> None:
    """2xx без пригодного конверта не даёт ссылки: исход неизвестен, повтор запрещён."""
    client, recorder = make_client(lambda request: _response(200, body))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert error.error.details == {"operation": "submit"}
    assert len(recorder.requests) == 1
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "../escape" not in str(error.error.model_dump())
    assert REMOTE_ID not in str(error.error.model_dump())


def test_submit_200_known_failed_result_is_not_masked_as_uncertain(make_client: Any) -> None:
    """Валидный ID + терминальный failed — известный исход, а не SUBMIT_UNCERTAIN."""
    body = _pending_body() | {"status": "failed", "error": {"code": "GENERATION_FAILED"}}
    client, recorder = make_client(lambda request: _response(200, body))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    assert excinfo.value.error.code == "REMOTE_GENERATION_FAILED"
    assert excinfo.value.error.retryable is None
    assert len(recorder.requests) == 1


def test_get_malformed_json_stays_invalid_response_not_uncertain(make_client: Any) -> None:
    """GET с неразбираемым телом остаётся PROVIDER_INVALID_RESPONSE, а не SUBMIT_UNCERTAIN."""

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client, _ = make_client(responder)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"


def test_replaced_reference_is_read_with_aggregate_cap_before_http(
    make_client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    png = png_bytes()
    first = _input_ref(tmp_path / "first.png", png, position=0)
    second = _input_ref(tmp_path / "second.png", png, position=1)
    # Prepared paths can be replaced after validation; neither metadata nor stat is trusted.
    second.path.write_bytes(b"x" * 1024)
    read_limits: list[int] = []
    original_open = Path.open

    class BoundedReader:
        def __init__(self, file: Any) -> None:
            self.file = file

        def __enter__(self) -> BoundedReader:
            self.file.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            self.file.__exit__(*args)

        def read(self, size: int = -1) -> bytes:
            read_limits.append(size)
            assert 0 < size <= cap + 1
            return self.file.read(size)

    def tracked_open(path: Path, *args: Any, **kwargs: Any) -> BoundedReader:
        return BoundedReader(original_open(path, *args, **kwargs))

    cap = len(png) + 8
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    monkeypatch.setattr(Path, "open", tracked_open)

    def mapping_must_not_run(*args: Any, **kwargs: Any) -> None:
        pytest.fail("oversized reference reached mapper")

    monkeypatch.setattr("aimedia.providers.polza.gateway.build_media_request", mapping_must_not_run)
    with pytest.raises(InvalidParameterValueError) as excinfo:
        run(_gateway(client, max_body_bytes=cap).submit(_request(images=[first, second])))
    assert read_limits == [cap + 1, 9]
    assert excinfo.value.code == "INVALID_PARAMETER_VALUE"
    assert second.path.name not in "".join(traceback.format_exception(excinfo.value))
    assert excinfo.value.__context__ is None
    assert recorder.requests == []


def test_submit_input_file_missing_is_pre_http_error(make_client: Any) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    missing = InputRef(kind=InputKind.IMAGE, path=Path("does/not/exist.png"), position=0)
    with pytest.raises(InputFileNotFoundError) as excinfo:
        run(gateway.submit(_request(images=[missing])))
    assert excinfo.value.__context__ is None
    assert "does/not/exist.png" not in "".join(traceback.format_exception(excinfo.value))
    assert recorder.requests == []


# --- GET статус и результат --------------------------------------------------


def test_get_status_uses_get_and_percent_encodes_id(make_client: Any) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    state = run(gateway.get_status(_media_ref()))
    assert state is ProviderJobState.SUBMITTED
    assert len(recorder.requests) == 1
    sent = recorder.requests[0]
    assert sent.method == "GET"
    assert str(sent.url) == f"{POLZA_API_BASE_URL}/media/{REMOTE_ID}"
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"


def test_fetch_result_normalizes_decimal_exactly(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _completed_body()))
    gateway = _gateway(client)
    result = run(gateway.fetch_result(_media_ref()))
    assert result.cost is not None
    assert isinstance(result.cost.amount, Decimal)
    assert result.cost.amount == Decimal("0.0831")
    assert result.cost.currency == "RUB"


def test_status_cancelled_is_typed_non_success(make_client: Any) -> None:
    client, _ = make_client(
        lambda request: _response(200, _pending_body() | {"status": "cancelled"})
    )
    gateway = _gateway(client)
    assert run(gateway.get_status(_media_ref())) is ProviderJobState.CANCELLED


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape",
        "a/b",
        "a?secret-id-canary",
        "a#fragment",
        "a%2Fb",
        "a b",
        "a" * 129,
        ".",
        "..",
    ],
    ids=[
        "traversal",
        "slash",
        "query",
        "fragment",
        "encoded",
        "space",
        "oversize",
        "dot",
        "dotdot",
    ],
)
def test_unsafe_remote_id_rejected_before_http(make_client: Any, unsafe: str) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref(unsafe)))
    assert excinfo.value.error.code == "PROVIDER_INVALID_REMOTE_REF"
    assert excinfo.value.error.details == {"reason": "invalid_remote_job_id"}
    # Ни одно поле ошибки не отражает переданный ID: `code`/`message`/`details` —
    # фиксированные константы. Утечку проверяем по динамическому `details`, а не по
    # всей сериализации: фиксированная русская формулировка `message` сама содержит
    # легитимную пунктуацию, включая «.».
    assert unsafe not in str(excinfo.value.error.details)
    assert recorder.requests == []


@pytest.mark.parametrize("operation", [None, "other"], ids=["missing", "other"])
@pytest.mark.parametrize("method", ["get_status", "fetch_result"])
def test_non_media_operation_rejected_before_http(
    make_client: Any, method: str, operation: str | None
) -> None:
    client, recorder = make_client(lambda request: _response(200, _completed_body()))
    gateway = _gateway(client)
    remote_ref = _media_ref().model_copy(update={"operation": operation})
    with pytest.raises(ProviderError) as excinfo:
        run(getattr(gateway, method)(remote_ref))
    assert excinfo.value.error.code == "PROVIDER_INVALID_REMOTE_REF"
    assert recorder.requests == []


@pytest.mark.parametrize("method", ["get_status", "fetch_result"])
def test_get_rejects_mismatched_response_id_before_normalization(
    make_client: Any, method: str
) -> None:
    different_id = "aig_other"
    client, recorder = make_client(
        lambda request: _response(200, _completed_body(id=different_id, status="failed"))
    )
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(getattr(gateway, method)(_media_ref()))
    error = excinfo.value
    assert error.error.code == "PROVIDER_INVALID_RESPONSE"
    assert error.error.details == {"reason": "remote_job_id_mismatch"}
    assert len(recorder.requests) == 1
    assert different_id not in str(error.error.model_dump())
    assert REMOTE_ID not in str(error.error.model_dump())
    assert error.__cause__ is None
    assert error.__context__ is None


def test_foreign_provider_ref_rejected_before_http(make_client: Any) -> None:
    client, recorder = make_client(lambda request: _response(200, _pending_body()))
    gateway = _gateway(client)
    foreign = RemoteJobRef(provider_id="other", remote_job_id=REMOTE_ID)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(foreign))
    assert excinfo.value.error.code == "PROVIDER_INVALID_REMOTE_REF"
    assert recorder.requests == []


# --- Редиректы: ключ не уходит на чужой host ---------------------------------


def test_redirect_to_foreign_host_is_rejected_without_forwarding_key(make_client: Any) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"https://evil.example/steal?sig={CANARY}"})

    client, recorder = make_client(responder, follow_redirects=True)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    assert excinfo.value.error.code == "PROVIDER_REDIRECT"
    assert excinfo.value.error.retryable is False
    assert len(recorder.requests) == 1
    assert all(req.url.host == "polza.ai" for req in recorder.requests)
    assert all(req.headers.get("authorization") == f"Bearer {API_KEY}" for req in recorder.requests)
    assert CANARY not in "".join(traceback.format_exception(excinfo.value))


def test_same_origin_redirect_is_also_rejected(make_client: Any) -> None:
    """follow_redirects=False действует даже при follow_redirects=True у клиента."""
    location = f"{POLZA_API_BASE_URL}/media/{REMOTE_ID}"

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": location})

    client, recorder = make_client(responder, follow_redirects=True)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_REDIRECT"
    assert len(recorder.requests) == 1


# --- HTTP-ошибки и trace ID --------------------------------------------------

_TRACE_ID = "550e8400-e29b-41d4-a716-446655440000"

# GET сохраняет существующие коды/retryable для всех HTTP-статусов.
_GET_HTTP_ERROR_CASES: list[tuple[int, str, bool | None]] = [
    (400, "PROVIDER_HTTP_ERROR", None),
    (401, "PROVIDER_AUTHENTICATION", False),
    (402, "PROVIDER_INSUFFICIENT_BALANCE", False),
    (403, "PROVIDER_FORBIDDEN", False),
    (408, "PROVIDER_TIMEOUT", True),
    (429, "PROVIDER_RATE_LIMIT", True),
    (500, "PROVIDER_UNAVAILABLE", True),
    (503, "PROVIDER_UNAVAILABLE", True),
]

# Явные отказы оплаченного POST остаются различимыми и не превращаются в
# SUBMIT_UNCERTAIN. 429 считается отказом до обработки (rate limit), поэтому
# сохраняет retryable=True; неизвестный исход требует retryable=None.
_SUBMIT_EXPLICIT_REJECTION_CASES: list[tuple[int, str, bool | None]] = [
    (400, "PROVIDER_HTTP_ERROR", None),
    (401, "PROVIDER_AUTHENTICATION", False),
    (402, "PROVIDER_INSUFFICIENT_BALANCE", False),
    (403, "PROVIDER_FORBIDDEN", False),
    (429, "PROVIDER_RATE_LIMIT", True),
]


def _error_responder(status: int) -> Callable[[httpx.Request], httpx.Response]:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=json.dumps(
                {
                    "error": {"code": "UNAUTHORIZED", "message": CANARY},
                    "trace_id": _TRACE_ID,
                }
            ).encode("utf-8"),
        )

    return responder


def _assert_redacted_trace_error(
    error: ProviderError, status: int, code: str, retryable: bool | None
) -> None:
    assert error.error.code == code
    assert error.error.retryable is retryable
    assert error.error.details == {"http_status": status, "trace_id": _TRACE_ID}
    assert error.__cause__ is None
    assert error.__context__ is None
    formatted = "".join(traceback.format_exception(error))
    assert CANARY not in formatted
    assert CANARY not in str(error.error.model_dump())
    assert "UNAUTHORIZED" not in str(error.error.model_dump())


@pytest.mark.parametrize(("status", "code", "retryable"), _GET_HTTP_ERROR_CASES)
def test_get_http_errors_are_typed_without_body_leak(
    make_client: Any, status: int, code: str, retryable: bool | None
) -> None:
    """GET сохраняет существующие коды и retryable для всех HTTP-статусов."""
    client, recorder = make_client(_error_responder(status))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))

    _assert_redacted_trace_error(excinfo.value, status, code, retryable)
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(("status", "code", "retryable"), _SUBMIT_EXPLICIT_REJECTION_CASES)
def test_submit_explicit_http_rejections_are_typed(
    make_client: Any, status: int, code: str, retryable: bool | None
) -> None:
    """Явный отказ провайдера на POST виден как отдельный код, а не как неизвестный исход."""
    client, recorder = make_client(_error_responder(status))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    _assert_redacted_trace_error(excinfo.value, status, code, retryable)
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [408, 500, 502, 503])
def test_submit_ambiguous_http_statuses_are_uncertain(make_client: Any, status: int) -> None:
    """408/5xx не доказывают непринятие оплаченного POST: повтор запрещён."""
    client, recorder = make_client(_error_responder(status))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert error.error.details == {"operation": "submit"}
    assert len(recorder.requests) == 1  # нет автоматического повторного POST
    assert error.__cause__ is None
    assert error.__context__ is None
    formatted = "".join(traceback.format_exception(error))
    assert CANARY not in formatted
    assert CANARY not in str(error.error.model_dump())


@pytest.mark.parametrize(
    "unsafe_trace",
    [
        f"https://cdn.example.invalid/x?sig={CANARY}",
        f"prompt {CANARY}",
        "x" * 129,
    ],
    ids=["signed-url", "prompt", "oversize"],
)
def test_unsafe_trace_id_is_not_allowlisted(make_client: Any, unsafe_trace: str) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        payload = {"error": {"trace_id": unsafe_trace}, "trace_id": unsafe_trace}
        return httpx.Response(500, content=json.dumps(payload).encode("utf-8"))

    client, _ = make_client(responder)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.details == {"http_status": 500}
    assert CANARY not in str(excinfo.value.error.model_dump())
    assert CANARY not in "".join(traceback.format_exception(excinfo.value))


def test_transport_timeout_on_status_is_timeout_not_balance(make_client: Any) -> None:
    """Сетевой timeout не маскируется под «недостаточно средств» (402)."""

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client, _ = make_client(failing)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_TIMEOUT"
    assert excinfo.value.error.retryable is True
    assert excinfo.value.error.details == {"operation": "status"}


def test_transport_error_on_status_is_generic(make_client: Any) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed")

    client, _ = make_client(failing)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_TRANSPORT_ERROR"


# --- Тело ответа и JSON ------------------------------------------------------


def test_malformed_json_is_invalid_response_without_body_leak(make_client: Any) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=f"not json {CANARY}".encode())

    client, _ = make_client(responder)
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert CANARY not in "".join(traceback.format_exception(excinfo.value))


def test_invalid_status_payload_is_invalid_response(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _pending_body() | {"status": "bogus"}))
    gateway = _gateway(client)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"


def test_response_body_too_large_is_rejected_before_json_parse(make_client: Any) -> None:
    """Большое тело отклоняется до разбора: даже невалидный JSON не парсится."""

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 200)

    client, _ = make_client(responder)
    gateway = _gateway(client, max_response_bytes=16)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert excinfo.value.error.details == {"max_response_bytes": 16}


class TrackedResponseStream(httpx.AsyncByteStream):
    """Позволяет проверить остановку чтения и закрытие HTTP-ответа без буферизации."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def test_compressed_response_rejected_before_read_and_closed(make_client: Any) -> None:
    stream = TrackedResponseStream([gzip.compress(b"x" * 100_000), b"must-not-be-read"])
    client, recorder = make_client(
        lambda request: httpx.Response(
            302,
            headers={"content-encoding": "gzip", "location": f"https://evil.example/{CANARY}"},
            stream=stream,
        ),
        follow_redirects=True,
    )
    with pytest.raises(ProviderError) as excinfo:
        run(_gateway(client, max_response_bytes=16).get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "unsupported_content_encoding"}
    assert excinfo.value.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(excinfo.value))
    assert stream.read_count == 0
    assert stream.closed
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["accept-encoding"] == "identity"
    assert recorder.requests[0].url.host == "polza.ai"


def test_get_2xx_unexpected_content_encoding_stays_invalid_response(make_client: Any) -> None:
    """GET с нечитаемым телом 2xx сохраняет существующий код PROVIDER_INVALID_RESPONSE."""
    stream = TrackedResponseStream([gzip.compress(f"body {CANARY}".encode())])
    client, recorder = make_client(
        lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
    )
    with pytest.raises(ProviderError) as excinfo:
        run(_gateway(client, max_response_bytes=16).get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "unsupported_content_encoding"}
    assert CANARY not in "".join(traceback.format_exception(excinfo.value))
    assert stream.read_count == 0
    assert stream.closed
    assert len(recorder.requests) == 1


def test_submit_2xx_unexpected_content_encoding_is_uncertain(make_client: Any) -> None:
    """Нечитаемое тело 2xx на POST: задание могло быть принято, повтор запрещён."""
    stream = TrackedResponseStream([gzip.compress(f"body {CANARY}".encode())])
    client, recorder = make_client(
        lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
    )
    with pytest.raises(ProviderError) as excinfo:
        run(_gateway(client, max_response_bytes=16).submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert error.error.details == {"operation": "submit"}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(error))
    assert stream.read_count == 0
    assert stream.closed
    assert len(recorder.requests) == 1  # нет автоматического повторного POST
    assert recorder.requests[0].headers["accept-encoding"] == "identity"


def test_submit_2xx_body_over_cap_is_uncertain(make_client: Any) -> None:
    """Тело 2xx сверх safety-лимита на POST: повтор запрещён, тело не читается дальше."""
    stream = TrackedResponseStream([f"{CANARY}".encode() + b"x" * 40, b"must-not-be-read"])
    client, recorder = make_client(lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(ProviderError) as excinfo:
        run(_gateway(client, max_response_bytes=16).submit(_request()))

    error = excinfo.value
    assert error.error.code == SUBMIT_UNCERTAIN
    assert error.error.retryable is None
    assert error.error.details == {"operation": "submit"}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(error))
    assert stream.read_count == 1
    assert stream.closed
    assert len(recorder.requests) == 1  # нет автоматического повторного POST


def test_plain_response_accepts_exact_cap_and_rejects_one_more(make_client: Any) -> None:
    body = json.dumps(_pending_body()).encode()
    client, _ = make_client(lambda request: httpx.Response(200, content=body))
    assert run(_gateway(client, max_response_bytes=len(body)).get_status(_media_ref())) is (
        ProviderJobState.SUBMITTED
    )
    with pytest.raises(ProviderError) as excinfo:
        run(_gateway(client, max_response_bytes=len(body) - 1).get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_RESPONSE_TOO_LARGE"


def test_oversized_stream_stops_before_next_chunk_and_closes(make_client: Any) -> None:
    stream = TrackedResponseStream([b"x" * 17, b"must-not-be-read"])
    client, _ = make_client(lambda request: httpx.Response(200, stream=stream))
    gateway = _gateway(client, max_response_bytes=16)
    with pytest.raises(ProviderError) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert excinfo.value.error.code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert stream.read_count == 1
    assert stream.closed


@pytest.mark.parametrize("invalid_json", [False, True], ids=["success", "invalid-json"])
def test_stream_closes_on_success_and_invalid_json(make_client: Any, invalid_json: bool) -> None:
    content = b"not json" if invalid_json else json.dumps(_pending_body()).encode()
    stream = TrackedResponseStream([content])
    client, _ = make_client(lambda request: httpx.Response(200, stream=stream))
    gateway = _gateway(client)
    if invalid_json:
        with pytest.raises(ProviderError) as excinfo:
            run(gateway.get_status(_media_ref()))
        assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    else:
        assert run(gateway.get_status(_media_ref())) is ProviderJobState.SUBMITTED
    assert stream.closed


@pytest.mark.parametrize(
    ("failure_factory", "expected"),
    [
        (lambda: httpx.ReadError(f"stream read failed: {CANARY}"), ProviderError),
        (lambda: asyncio.CancelledError(), asyncio.CancelledError),
    ],
    ids=["read-error", "cancelled"],
)
def test_stream_closes_on_read_error_or_cancellation(
    make_client: Any,
    failure_factory: Callable[[], BaseException],
    expected: type[BaseException],
) -> None:
    class FailingStream(TrackedResponseStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{"
            raise failure_factory()

    stream = FailingStream([])
    client, _ = make_client(lambda request: httpx.Response(200, stream=stream))
    gateway = _gateway(client)
    with pytest.raises(expected) as excinfo:
        run(gateway.get_status(_media_ref()))
    assert stream.closed
    if expected is ProviderError:
        error = excinfo.value
        assert error.error.code == "PROVIDER_TRANSPORT_ERROR"
        assert error.__cause__ is None
        assert error.__context__ is None
        assert CANARY not in "".join(traceback.format_exception(error))


def test_request_and_response_caps_must_be_positive(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _pending_body()))
    for bad in (0, -1, True):
        with pytest.raises(ValueError):
            _gateway(client, max_body_bytes=bad)
        with pytest.raises(ValueError):
            _gateway(client, max_response_bytes=bad)


def test_empty_api_key_is_rejected(make_client: Any) -> None:
    client, _ = make_client(lambda request: _response(200, _pending_body()))
    with pytest.raises(ValueError):
        PolzaProviderGateway(
            client=client,
            api_key="",
            effective=_effective(),
            max_body_bytes=HUGE_BODY_BYTES,
            max_response_bytes=HUGE_RESPONSE_BYTES,
        )


def test_gateway_imports_no_test_support() -> None:
    """Производственный шлюз не тянет tests/support."""
    tree = ast.parse(GATEWAY_MODULE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "tests" not in roots
    assert "support" not in roots


def test_multiple_images_keep_order_in_body(make_client: Any, tmp_path: Path) -> None:
    png = png_bytes()
    jpeg = jpeg_bytes()

    def responder(request: httpx.Request) -> httpx.Response:
        return _response(200, _pending_body())

    client, recorder = make_client(responder)
    gateway = _gateway(client)
    request = _request(
        images=[
            _input_ref(tmp_path / "a.png", png, position=0),
            _input_ref(tmp_path / "b.jpg", jpeg, position=1),
        ]
    )
    assert (tmp_path / "a.png").read_bytes() == png
    assert (tmp_path / "b.jpg").read_bytes() == jpeg
    run(gateway.submit(request))
    payload = json.loads(recorder.requests[0].content)
    images = payload["input"]["images"]
    assert images[0]["data"].startswith("data:image/png;base64,")
    assert images[1]["data"].startswith("data:image/jpeg;base64,")
