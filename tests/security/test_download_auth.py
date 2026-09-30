"""Безопасность скачивания remote artifact Polza (E06, C09c2) 🔒.

Проверяется граница C09c2: bearer Polza не уходит на CDN, SSRF закрыт
connection-bound DNS-закреплением, редиректы и автоматические повторы не
выполняются, тело ограничено и закрыто на всех путях, а подписанный URL не попадает
ни в диагностику, ни в DEBUG-журнал транспорта.

Реальный API и сеть не используются: `FakeResolver` возвращает заданные адреса,
`RecordingBackend` отдаёт scripted HTTP-ответ. Offline-guard из `tests/conftest.py`
дополнительно запрещает любой внешний сокет.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import ssl
import time
import traceback
from collections.abc import Coroutine, Sequence
from typing import Any

import httpcore
import pytest
from download_backend import (
    PUBLIC_IPV4,
    PUBLIC_IPV6,
    FakeResolver,
    RecordingBackend,
    chunked_body,
    chunked_head,
    http_head,
    raw_http_response,
)
from image_fixtures import png_bytes

from aimedia.domain.artifacts import ArtifactKind, RemoteArtifact
from aimedia.domain.errors import ProviderError
from aimedia.providers.polza.download import (
    ARTIFACT_DOWNLOAD_FAILED,
    DnsPinningBackend,
    PolzaArtifactDownloader,
)

HOST = "s3.polza.ai"
ARTIFACT_URL = f"https://{HOST}/f/205141/2026/03/aig_synthetic.png"
SIGNATURE_CANARY = "signed-query-canary-value"

PNG = png_bytes()


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


class Harness:
    """Связка downloader + фейковые резолвер/backend для одного сценария."""

    def __init__(
        self,
        *,
        chunks: Sequence[bytes] | None = None,
        resolver: FakeResolver | None = None,
        read_error: BaseException | None = None,
        connect_error: BaseException | None = None,
        max_artifact_bytes: int = 1024 * 1024,
        timeout_seconds: float | None = None,
    ) -> None:
        self.resolver = resolver if resolver is not None else FakeResolver(default=[PUBLIC_IPV4])
        self.backend = RecordingBackend(
            chunks if chunks is not None else [raw_http_response(200, PNG)],
            read_error=read_error,
            connect_error=connect_error,
        )
        self.downloader = PolzaArtifactDownloader(
            max_artifact_bytes=max_artifact_bytes,
            timeout_seconds=timeout_seconds,
            resolver=self.resolver,
            direct_backend=self.backend,
        )

    def fetch(self, url: str = ARTIFACT_URL) -> Any:
        artifact = RemoteArtifact(kind=ArtifactKind.IMAGE, url=url)
        return run(self.downloader.fetch(artifact))

    def close(self) -> None:
        run(self.downloader.aclose())


# --- SSRF: connection-bound DNS-закрепление -----------------------------------


def test_dns_pins_public_ip_and_never_uses_hostname() -> None:
    harness = Harness()
    try:
        assert harness.fetch() == PNG
        # Прямому backend уходит числовой адрес из ответа резолвера, а не hostname.
        assert harness.backend.connects == [(PUBLIC_IPV4, 443)]
        assert harness.resolver.calls == [(HOST, 443)]
    finally:
        harness.close()


def test_sni_uses_original_host_and_strict_tls_context() -> None:
    harness = Harness()
    try:
        assert harness.fetch() == PNG
        tls_context, server_hostname = harness.backend.streams[0].tls[0]
        # SNI и проверка сертификата идут по исходному hostname, а не по числовому IP.
        assert server_hostname == HOST
        assert tls_context.verify_mode == ssl.CERT_REQUIRED
        assert tls_context.check_hostname is True
    finally:
        harness.close()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback IPv4
        "10.0.0.1",  # private RFC1918
        "192.168.1.10",  # private RFC1918
        "169.254.169.254",  # link-local / cloud metadata
        "100.64.0.1",  # CGNAT: is_private ложно, но не глобально-публичный
        "0.0.0.0",  # unspecified
        "255.255.255.255",  # reserved broadcast
        "192.0.2.5",  # TEST-NET-1
        "::1",  # loopback IPv6
        "fe80::1",  # link-local IPv6
        "fd00::1",  # unique-local IPv6
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "ff02::1",  # multicast IPv6 (Python считает is_global=True)
        "64:ff9b::a00:1",  # NAT64 reserved
        "not-an-ip-address",  # мусор от резолвера
    ],
)
def test_non_public_answers_are_rejected_before_connect(address: str) -> None:
    harness = Harness(resolver=FakeResolver(default=[address]))
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.code == ARTIFACT_DOWNLOAD_FAILED
        assert excinfo.value.error.details["reason"] == "unsafe_address"
        assert harness.backend.connects == []  # TCP не устанавливается
    finally:
        harness.close()


def test_mixed_public_and_private_answers_reject_the_whole_resolution() -> None:
    harness = Harness(resolver=FakeResolver(default=[PUBLIC_IPV4, "127.0.0.1"]))
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "unsafe_address"
        assert harness.backend.connects == []
    finally:
        harness.close()


def test_public_ipv6_answer_is_allowed_and_pinned() -> None:
    harness = Harness(resolver=FakeResolver(default=[PUBLIC_IPV6]))
    try:
        assert harness.fetch() == PNG
        assert harness.backend.connects == [(PUBLIC_IPV6, 443)]
    finally:
        harness.close()


@pytest.mark.parametrize("failure", [None, OSError("dns down")], ids=["empty", "oserror"])
def test_unresolvable_host_is_typed_without_connect(failure: OSError | None) -> None:
    resolver = FakeResolver(failure=failure)
    harness = Harness(resolver=resolver)
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "unresolvable_host"
        assert harness.backend.connects == []
    finally:
        harness.close()


def test_unknown_host_is_rejected_before_any_dns_lookup() -> None:
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch("https://evil.example/aig_synthetic.png")
        assert excinfo.value.error.details["reason"] == "unapproved_host"
        assert harness.resolver.calls == []  # DNS не вызывается
        assert harness.backend.connects == []
    finally:
        harness.close()


def test_hanging_resolver_is_bounded_by_timeout() -> None:
    resolver = FakeResolver(default=[PUBLIC_IPV4], hang_seconds=3600.0)
    harness = Harness(resolver=resolver, timeout_seconds=0.05)
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        elapsed = time.monotonic() - started
        error = excinfo.value
        assert error.error.details["reason"] == "timeout"
        assert error.error.retryable is True
        assert error.__cause__ is None
        assert error.__context__ is None
        assert HOST not in str(error.error.model_dump())
        assert elapsed < 5.0  # зависший DNS не обходит бюджет
    finally:
        harness.close()


def test_unix_socket_backend_is_not_supported() -> None:
    backend = DnsPinningBackend(
        direct=RecordingBackend([]), resolver=FakeResolver(), dns_timeout=5.0
    )
    with pytest.raises(ProviderError) as excinfo:
        run(backend.connect_unix_socket("/tmp/socket"))
    assert excinfo.value.error.code == ARTIFACT_DOWNLOAD_FAILED


# --- Пул: безопасный backend — внутренний инвариант ---------------------------


def test_downloader_does_not_accept_an_injected_pool() -> None:
    assert "pool" not in inspect.signature(PolzaArtifactDownloader).parameters
    with pytest.raises(TypeError):
        PolzaArtifactDownloader(max_artifact_bytes=1024, pool=object())  # type: ignore[call-arg]


def test_internal_pool_is_always_built_over_dns_pinning_backend() -> None:
    harness = Harness()
    try:
        # Обычный пул передать нельзя: backend создаётся самим downloader-ом.
        assert isinstance(harness.downloader._pool._network_backend, DnsPinningBackend)
    finally:
        harness.close()


# --- Bearer, cookie и proxy не уходят на CDN ----------------------------------


def test_bearer_cookie_and_api_key_are_absent_from_cdn_request() -> None:
    harness = Harness()
    try:
        harness.fetch()
        request_bytes = harness.backend.streams[0].request_bytes
        lowered = request_bytes.lower()
        assert b"authorization" not in lowered
        assert b"cookie" not in lowered
        assert b"x-api-key" not in lowered
        assert b"accept-encoding: identity" in lowered
        assert f"host: {HOST}".encode() in lowered
    finally:
        harness.close()


def test_proxy_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "HTTP_PROXY": "http://proxy-canary.invalid:3128",
        "HTTPS_PROXY": "http://proxy-canary.invalid:3128",
        "ALL_PROXY": "http://proxy-canary.invalid:3128",
        "NO_PROXY": HOST,
    }.items():
        monkeypatch.setenv(name, value)
    harness = Harness()
    try:
        assert harness.fetch() == PNG
        # Подключение идёт напрямую к закреплённому IP, proxy env не читается.
        assert harness.backend.connects == [(PUBLIC_IPV4, 443)]
        request_bytes = harness.backend.streams[0].request_bytes
        assert b"proxy-canary" not in request_bytes
        assert b"proxy-authorization" not in request_bytes.lower()
    finally:
        harness.close()


# --- Redaction подписанного URL -----------------------------------------------


def test_signed_url_is_redacted_from_error_traceback_and_context() -> None:
    signed_url = f"{ARTIFACT_URL}?X-Amz-Signature={SIGNATURE_CANARY}"
    harness = Harness(chunks=[raw_http_response(403, b"denied")])
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(signed_url)
        error = excinfo.value
        assert error.error.code == ARTIFACT_DOWNLOAD_FAILED
        assert error.__cause__ is None
        assert error.__context__ is None
        for text in (
            "".join(traceback.format_exception(error)),
            str(error),
            str(error.error.message),
            str(error.error.details),
            str(error.error.model_dump()),
            repr(error),
        ):
            assert SIGNATURE_CANARY not in text
            assert "X-Amz-Signature" not in text
    finally:
        harness.close()


def test_transport_error_text_with_signed_url_is_not_chained() -> None:
    signed_url = f"{ARTIFACT_URL}?X-Amz-Signature={SIGNATURE_CANARY}"
    harness = Harness(
        chunks=[http_head(200, content_length=4096), b"partial"],
        read_error=httpcore.ReadError(f"read failed for {signed_url}"),
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(signed_url)
        error = excinfo.value
        assert error.error.details["reason"] == "transport_error"
        assert error.__cause__ is None
        assert error.__context__ is None
        assert SIGNATURE_CANARY not in "".join(traceback.format_exception(error))
    finally:
        harness.close()


# --- Политика логирования транспорта ------------------------------------------


def test_debug_logging_does_not_expose_signed_url(caplog: pytest.LogCaptureFixture) -> None:
    signed_url = f"{ARTIFACT_URL}?X-Amz-Signature={SIGNATURE_CANARY}"
    harness = Harness(
        chunks=[
            http_head(307, headers=[(b"location", signed_url.encode())], content_length=0),
        ]
    )
    try:
        assert logging.getLogger("httpcore").level >= logging.WARNING
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ProviderError) as excinfo:
                harness.fetch(signed_url)
        assert excinfo.value.error.details["reason"] == "redirect"
        assert SIGNATURE_CANARY not in caplog.text
    finally:
        harness.close()


def test_raw_httpcore_debug_would_leak_signed_url(caplog: pytest.LogCaptureFixture) -> None:
    """Вектор реален: без политики httpcore на DEBUG печатает target и Location."""
    signed_url = f"{ARTIFACT_URL}?X-Amz-Signature={SIGNATURE_CANARY}"
    harness = Harness(
        chunks=[
            http_head(307, headers=[(b"location", signed_url.encode())], content_length=0),
        ]
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="httpcore"):
            with pytest.raises(ProviderError):
                harness.fetch(signed_url)
        assert SIGNATURE_CANARY in caplog.text  # политика уровня — то, что снимает утечку
    finally:
        harness.close()


# --- Редиректы и повторы -------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_never_followed_even_to_same_host(status: int) -> None:
    location = f"https://{HOST}/elsewhere/other.png"
    harness = Harness(
        chunks=[http_head(status, headers=[(b"location", location.encode())], content_length=0)]
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "redirect"
        assert excinfo.value.error.details["http_status"] == status
        # Никакого второго соединения: редирект не отслеживается вообще.
        assert harness.backend.connects == [(PUBLIC_IPV4, 443)]
        assert len(harness.backend.streams) == 1
        assert "elsewhere" not in str(excinfo.value.error.model_dump())
    finally:
        harness.close()


def test_no_automatic_retry_on_transport_failure() -> None:
    harness = Harness(
        chunks=[http_head(200, content_length=64), b"partial"],
        read_error=httpcore.ReadError("connection reset"),
    )
    try:
        with pytest.raises(ProviderError):
            harness.fetch()
        assert len(harness.backend.connects) == 1  # ровно одна попытка
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("error", "reason", "retryable"),
    [
        (httpcore.ConnectError("refused"), "transport_error", False),
        (httpcore.ConnectTimeout("connect timed out"), "timeout", True),
    ],
    ids=["connect-error", "connect-timeout"],
)
def test_connect_failures_are_typed(
    error: httpcore.NetworkError, reason: str, retryable: bool
) -> None:
    harness = Harness(connect_error=error)
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == reason
        assert excinfo.value.error.retryable is retryable
        assert len(harness.backend.connects) == 1  # retries=0
    finally:
        harness.close()


def test_read_timeout_is_typed_as_timeout() -> None:
    harness = Harness(
        chunks=[http_head(200, content_length=4096), b"partial"],
        read_error=httpcore.ReadTimeout("read timed out"),
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "timeout"
        assert excinfo.value.error.retryable is True
    finally:
        harness.close()


def test_malformed_cdn_response_is_typed_without_raw_bytes() -> None:
    canary = "RAW-RESPONSE-CANARY"
    harness = Harness(chunks=[f"not a valid HTTP response {canary}\r\n".encode()])
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        error = excinfo.value
        assert error.error.details["reason"] == "invalid_response"
        assert error.__cause__ is None
        assert error.__context__ is None
        assert canary not in "".join(traceback.format_exception(error))
    finally:
        harness.close()


# --- Ограничение тела и закрытие ----------------------------------------------


def test_unsupported_content_encoding_is_rejected_before_body_read() -> None:
    harness = Harness(
        chunks=[
            http_head(200, headers=[(b"content-encoding", b"gzip")], content_length=len(PNG)),
            PNG,
        ]
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "unsupported_content_encoding"
        # Заголовки прочитаны, тело — нет: декомпрессии и чтения байтов не было.
        assert len(harness.backend.streams[0].served) == 1
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


def test_oversized_content_length_on_real_http_path_is_typed() -> None:
    # h11 отклоняет длину >20 цифр до выдачи Response; downloader типизирует это
    # безопасно, без текста протокола и без сырых байт.
    raw = b"HTTP/1.1 200 OK\r\ncontent-length: " + b"9" * 21 + b"\r\n\r\n"
    harness = Harness(chunks=[raw])
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        error = excinfo.value
        assert error.error.details["reason"] in {"invalid_response", "transport_error"}
        assert error.__cause__ is None
        assert error.__context__ is None
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


def test_raw_body_cap_is_enforced_before_join() -> None:
    # Chunked-ответ не несёт Content-Length, поэтому проверяется именно потоковый cap.
    harness = Harness(
        chunks=[chunked_head(), chunked_body(b"z" * 4096)],
        max_artifact_bytes=1024,
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "too_large"
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


def test_declared_content_length_over_cap_is_rejected_without_reading_body() -> None:
    harness = Harness(
        chunks=[http_head(200, content_length=4_000_000), b"z" * 4096],
        max_artifact_bytes=1024,
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch()
        assert excinfo.value.error.details["reason"] == "too_large"
        assert harness.backend.streams[0].served == [
            http_head(200, content_length=4_000_000)
        ]  # тело не запрашивалось
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


@pytest.mark.parametrize("status", [404, 500, 503])
def test_http_error_body_is_fully_read_and_connection_pooled(status: int) -> None:
    harness = Harness(chunks=[raw_http_response(status, b"error")])
    try:
        with pytest.raises(ProviderError):
            harness.fetch()
        # Полное тело вернуло соединение в пул; после закрытия пула сокет закрыт.
        harness.close()
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


def test_successful_fetch_closes_connection_on_pool_close() -> None:
    harness = Harness()
    try:
        assert harness.fetch() == PNG
        assert len(harness.backend.connects) == 1
        harness.close()
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()


def test_close_on_cancellation() -> None:
    harness = Harness(
        chunks=[http_head(200, content_length=4096), b"partial"],
        read_error=asyncio.CancelledError(),
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            harness.fetch()
        assert harness.backend.streams[0].closed is True
    finally:
        harness.close()
