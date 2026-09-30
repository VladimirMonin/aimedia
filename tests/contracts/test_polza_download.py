"""Контрактные offline-тесты скачивания remote artifact Polza (E06, C09c2).

Проверяется `aimedia.providers.polza.download.PolzaArtifactDownloader`: только
документированная форма доставки (`https` URL одобренного CDN), отклонение
неизвестных/insecure URL до DNS, advisory-роль `Content-Type`, типизация HTTP-ошибок,
безопасная валидация таймаута и заголовков, отсутствие доступа к файловой
системе/окружению. Фактический MIME и контейнер проверяются downstream по байтам —
тест это фиксирует через `probe_image`.

Сеть не используется: `RecordingBackend` отдаёт scripted HTTP-ответ, `FakeResolver`
возвращает публичный адрес, `ScriptedResponseStream` позволяет проверить `_consume`
напрямую. Все model/remote ID и адреса синтетические.
"""

from __future__ import annotations

import ast
import asyncio
import math
import socket
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any

import pytest
from download_backend import (
    PUBLIC_IPV4,
    FakeResolver,
    RecordingBackend,
    ScriptedResponseStream,
    raw_http_response,
    response_with,
)
from image_fixtures import png_bytes

from aimedia.application.inputs.image_probe import probe_image
from aimedia.domain.artifacts import ArtifactKind, RemoteArtifact
from aimedia.domain.errors import ProviderError
from aimedia.providers.polza.download import (
    APPROVED_ARTIFACT_HOSTS,
    ARTIFACT_DOWNLOAD_FAILED,
    DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    MAX_ARTIFACT_URL_LENGTH,
    DnsPinningBackend,
    PolzaArtifactDownloader,
    _content_length,
    resolve_public_addresses,
)

HOST = "s3.polza.ai"
ARTIFACT_URL = f"https://{HOST}/f/205141/2026/03/aig_synthetic.png"
PNG = png_bytes()
CANARY = "signed-query-canary-value"

DOWNLOAD_MODULE = (
    Path(__file__).resolve().parents[2] / "src" / "aimedia" / "providers" / "polza" / "download.py"
)


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


class Harness:
    def __init__(
        self,
        *,
        chunks: Sequence[bytes] | None = None,
        resolver: FakeResolver | None = None,
        max_artifact_bytes: int = 1024 * 1024,
    ) -> None:
        self.resolver = resolver if resolver is not None else FakeResolver(default=[PUBLIC_IPV4])
        self.backend = RecordingBackend(
            chunks if chunks is not None else [raw_http_response(200, PNG)]
        )
        self.downloader = PolzaArtifactDownloader(
            max_artifact_bytes=max_artifact_bytes,
            resolver=self.resolver,
            direct_backend=self.backend,
        )

    def fetch(self, url: str) -> Any:
        return run(self.downloader.fetch(RemoteArtifact(kind=ArtifactKind.IMAGE, url=url)))

    def close(self) -> None:
        run(self.downloader.aclose())


def _artifact(**fields: object) -> RemoteArtifact:
    payload: dict[str, object] = {"kind": ArtifactKind.IMAGE, "url": ARTIFACT_URL}
    payload.update(fields)
    return RemoteArtifact.model_validate(payload)


# --- Успешное получение и downstream-проверка ---------------------------------


def test_valid_tiny_png_bytes_pass_through_for_downstream_verification() -> None:
    harness = Harness()
    try:
        data = harness.fetch(ARTIFACT_URL)
        assert data == PNG
        # MIME и контейнер определяет downstream по фактическим байтам.
        assert probe_image(data).mime_type == "image/png"
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("url", "host"),
    [
        (f"https://{HOST}/f/aig.png", HOST),
        ("https://S3.POLZA.AI/f/aig.png", HOST),
        (f"https://{HOST}:443/f/aig.png", HOST),
    ],
    ids=["documented", "uppercase", "explicit-port"],
)
def test_documented_url_forms_are_accepted(url: str, host: str) -> None:
    harness = Harness()
    try:
        assert harness.fetch(url) == PNG
        assert harness.resolver.calls == [(host, 443)]
        assert f"host: {host}".encode() in harness.backend.streams[0].request_bytes.lower()
    finally:
        harness.close()


def test_signed_query_is_forwarded_to_approved_origin() -> None:
    harness = Harness()
    try:
        harness.fetch(f"https://{HOST}/f/aig.png?X-Amz-Signature={CANARY}&Expires=1")
        request_line = harness.backend.streams[0].request_bytes.split(b"\r\n", 1)[0]
        assert b"X-Amz-Signature=" in request_line
    finally:
        harness.close()


def test_content_type_is_advisory_not_proof() -> None:
    harness = Harness(
        chunks=[raw_http_response(200, PNG, headers=[(b"content-type", b"text/html")])]
    )
    try:
        # Downloader не доверяет Content-Type: формат подтверждает downstream по байтам.
        assert harness.fetch(ARTIFACT_URL) == PNG
    finally:
        harness.close()


# --- Форма доставки: только документированный URL -----------------------------


@pytest.mark.parametrize(
    "artifact",
    [
        _artifact(url=None),
        _artifact(provider_file_id="file_123"),
        _artifact(base64_data="aGVsbG8="),
    ],
    ids=["missing-url", "provider-file-id", "inline-base64"],
)
def test_undocumented_delivery_forms_are_rejected(artifact: RemoteArtifact) -> None:
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            run(harness.downloader.fetch(artifact))
        assert excinfo.value.error.details["reason"] in {"missing_delivery", "unsupported_delivery"}
        assert harness.resolver.calls == []
    finally:
        harness.close()


def test_non_image_kind_is_rejected() -> None:
    harness = Harness()
    try:
        artifact = RemoteArtifact.model_construct(kind="audio", url=f"https://{HOST}/a.mp3")
        with pytest.raises(ProviderError) as excinfo:
            run(harness.downloader.fetch(artifact))
        assert excinfo.value.error.details["reason"] == "unsupported_artifact_kind"
    finally:
        harness.close()


# --- URL-валидация: fail closed -----------------------------------------------


_INSECURE_URLS = [
    "http://s3.polza.ai/f/a.png",
    "ftp://s3.polza.ai/f/a.png",
    "//s3.polza.ai/f/a.png",
    "https://s3.polza.ai:8443/f/a.png",
    "https://s3.polza.ai:notaport/f/a.png",
    "https://user@s3.polza.ai/f/a.png",
    "https://user:secret@s3.polza.ai/f/a.png",
    "https://s3.polza.ai/f/a.png#fragment",
    "https://1.2.3.4/f/a.png",
    "https://[::1]/f/a.png",
    "https://s3.polza.ai/f/a\\b.png",
    "https://s3.polza.ai/f/a b.png",
    "https://s3.polza.ai/f/a\x00b.png",
    "",
    " ",
]


@pytest.mark.parametrize("url", _INSECURE_URLS, ids=repr)
def test_insecure_url_is_rejected_before_dns(url: str) -> None:
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(url)
        assert excinfo.value.error.code == ARTIFACT_DOWNLOAD_FAILED
        # Все URL матрицы используют одобренный host с синтаксическим нарушением,
        # поэтому причина — именно insecure_url, а не unapproved_host.
        assert excinfo.value.error.details["reason"] == "insecure_url"
        assert harness.resolver.calls == []
        assert harness.backend.connects == []
    finally:
        harness.close()


def test_overlong_url_is_rejected_before_parse() -> None:
    long_url = f"https://{HOST}/" + "a" * MAX_ARTIFACT_URL_LENGTH
    assert len(long_url) > MAX_ARTIFACT_URL_LENGTH
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(long_url)
        assert excinfo.value.error.details["reason"] == "insecure_url"
        assert harness.resolver.calls == []
    finally:
        harness.close()


@pytest.mark.parametrize(
    "host",
    ["evil.example", "cdn.polza.ai.evil.example", "polza.ai", "cdn-polza.ai", "cdn.polza.ai."],
)
def test_unknown_hosts_fail_closed_without_wildcard(host: str) -> None:
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(f"https://{host}/f/a.png")
        assert excinfo.value.error.details["reason"] == "unapproved_host"
        assert harness.resolver.calls == []
    finally:
        harness.close()


def test_undocumented_cdn_host_is_rejected_before_dns_and_http() -> None:
    # `cdn.polza.ai` не подтверждён документацией (`docs/Get Media.txt` -> s3.polza.ai):
    # отказ до DNS и до любого HTTP, без wildcard.
    harness = Harness()
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch("https://cdn.polza.ai/f/a.png")
        assert excinfo.value.error.details["reason"] == "unapproved_host"
        assert harness.resolver.calls == []
        assert harness.backend.connects == []
    finally:
        harness.close()


def test_approved_hosts_constant_is_exact() -> None:
    assert APPROVED_ARTIFACT_HOSTS == frozenset({"s3.polza.ai"})


# --- HTTP-ошибки --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (403, False), (404, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_http_error_is_typed(status: int, retryable: bool) -> None:
    harness = Harness(chunks=[raw_http_response(status, b"error")])
    try:
        with pytest.raises(ProviderError) as excinfo:
            harness.fetch(ARTIFACT_URL)
        error = excinfo.value.error
        assert error.code == ARTIFACT_DOWNLOAD_FAILED
        assert error.details["reason"] == "http_error"
        assert error.details["http_status"] == status
        assert error.retryable is retryable
    finally:
        harness.close()


def test_2xx_empty_body_is_returned_as_empty_bytes() -> None:
    harness = Harness(chunks=[raw_http_response(200, b"")])
    try:
        assert harness.fetch(ARTIFACT_URL) == b""
    finally:
        harness.close()


# --- `_consume`: разбор заголовков внутри guarded close -----------------------


def _consume(response: Any, *, max_artifact_bytes: int = 1024 * 1024) -> tuple[Any, Any]:
    downloader = PolzaArtifactDownloader(
        max_artifact_bytes=max_artifact_bytes,
        resolver=FakeResolver(default=[PUBLIC_IPV4]),
        direct_backend=RecordingBackend([]),
    )
    result: Any = None
    error: ProviderError | None = None
    try:
        result = run(downloader._consume(response))
    except ProviderError as exc:
        error = exc
    finally:
        run(downloader.aclose())
    return result, error


def test_consume_always_closes_the_response() -> None:
    # Прямая проверка инварианта `finally`: ответ закрыт на успехе, редиректе и HTTP-ошибке.
    for status in (200, 206, 307, 404, 503):
        stream = ScriptedResponseStream([PNG])
        response = response_with(
            stream, status=status, headers=[(b"content-length", str(len(PNG)).encode())]
        )
        result, error = _consume(response)
        assert stream.closed is True, status
        if 200 <= status < 300:
            assert result == PNG
            assert error is None
        else:
            assert result is None
            assert error is not None


def test_consume_closes_response_on_hostile_content_length() -> None:
    # 5000 цифр превышают лимит int() (4300); разбор обязан не падать и закрыть ответ.
    stream = ScriptedResponseStream([PNG])
    response = response_with(stream, headers=[(b"content-length", b"9" * 5000)])
    result, error = _consume(response)
    assert error is None
    assert result == PNG
    assert stream.closed is True


def test_consume_rejects_and_closes_on_huge_valid_content_length() -> None:
    stream = ScriptedResponseStream([PNG])
    response = response_with(stream, headers=[(b"content-length", b"99999999999999999999")])
    result, error = _consume(response, max_artifact_bytes=1024)
    assert result is None
    assert error is not None
    assert error.error.details["reason"] == "too_large"
    assert stream.closed is True


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-encoding", b"identity"), (b"content-encoding", b"gzip")],
        [(b"content-encoding", b"identity, gzip")],
        [(b"content-encoding", b"GZIP")],
    ],
    ids=["two-headers", "comma-separated", "mixed-case"],
)
def test_consume_rejects_any_non_identity_content_encoding(
    headers: list[tuple[bytes, bytes]],
) -> None:
    stream = ScriptedResponseStream([PNG])
    response = response_with(stream, headers=headers)
    result, error = _consume(response)
    assert result is None
    assert error is not None
    assert error.error.details["reason"] == "unsupported_content_encoding"
    assert stream.closed is True


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"content-length", b"6")], 6),
        ([(b"content-length", b"  6 ")], 6),
        ([(b"content-length", b"6"), (b"content-length", b"6")], None),
        ([(b"content-length", b"6"), (b"content-length", b"7")], None),
        ([(b"content-length", b"\xd9\xa3".decode("latin-1").encode())], None),
        ([(b"content-length", b"9" * 21)], None),
        ([(b"content-length", b"-1")], None),
        ([], None),
    ],
    ids=[
        "plain",
        "spaces",
        "duplicate",
        "conflicting",
        "non-ascii",
        "too-many-digits",
        "negative",
        "absent",
    ],
)
def test_content_length_parsing_is_conservative(
    headers: list[tuple[bytes, bytes]], expected: int | None
) -> None:
    assert _content_length(headers) == expected


# --- Конструктор и границы модуля ---------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, True, float("nan"), float("inf"), float("-inf"), "5"])
def test_timeout_must_be_finite_positive(bad: object) -> None:
    with pytest.raises(ValueError):
        PolzaArtifactDownloader(max_artifact_bytes=1024, timeout_seconds=bad)  # type: ignore[arg-type]


def test_default_timeout_is_finite_and_positive() -> None:
    assert isinstance(DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, float)
    assert math.isfinite(DEFAULT_DOWNLOAD_TIMEOUT_SECONDS)
    assert DEFAULT_DOWNLOAD_TIMEOUT_SECONDS > 0


@pytest.mark.parametrize("bad", [0, -1, True])
def test_max_artifact_bytes_must_be_positive(bad: int) -> None:
    with pytest.raises(ValueError):
        PolzaArtifactDownloader(max_artifact_bytes=bad)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True])
def test_dns_timeout_must_be_finite_positive(bad: object) -> None:
    with pytest.raises(ValueError):
        DnsPinningBackend(direct=RecordingBackend([]), resolver=FakeResolver(), dns_timeout=bad)  # type: ignore[arg-type]


def test_default_resolver_reports_addresses_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (b"bytes-address", 443)),
    ]

    async def scenario() -> Sequence[str]:
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(host: str, port: int, **kwargs: object) -> list[object]:
            return list(infos)

        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        return await resolve_public_addresses("s3.polza.ai", 443)

    # Дедупликация без потери порядка и игнорирование неподходящих записей.
    assert asyncio.run(scenario()) == ("93.184.216.34", "2606:2800::1")


def test_repr_does_not_leak_artifact_url() -> None:
    harness = Harness()
    try:
        rendered = repr(harness.downloader)
        assert HOST not in rendered
        assert "https://" not in rendered
    finally:
        harness.close()


def test_module_has_no_filesystem_or_environment_access() -> None:
    """Downloader не пишет файлов и не читает окружение: байты отдаёт вызывающей стороне."""
    tree = ast.parse(DOWNLOAD_MODULE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    open_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"open", "exec", "eval"}:
                open_calls += 1
    forbidden = {"os", "pathlib", "subprocess", "shutil", "tempfile", "httpx", "tests", "support"}
    assert roots.isdisjoint(forbidden)
    assert open_calls == 0
    assert "httpcore" in roots


def test_module_never_sends_authorization_or_proxy_headers() -> None:
    """Bearer Polza и cookie/proxy-заголовки не добавляются к CDN-запросу."""
    tree = ast.parse(DOWNLOAD_MODULE.read_text(encoding="utf-8"))
    header_names: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_httpcore_request(node.func)):
            continue
        for keyword in node.keywords:
            if keyword.arg != "headers":
                continue
            assert isinstance(keyword.value, ast.List), "headers должны быть явным списком"
            for element in keyword.value.elts:
                assert isinstance(element, ast.Tuple) and len(element.elts) == 2
                header_names.append(_as_text(element.elts[0].value).lower())
    assert header_names, "запрос должен задавать headers явно"
    assert set(header_names).isdisjoint(
        {"authorization", "cookie", "proxy-authorization", "x-api-key"}
    )
    assert header_names.count("accept-encoding") == 1


def _is_httpcore_request(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and ast.unparse(func) == "httpcore.Request"


def _as_text(value: object) -> str:
    assert isinstance(value, (str, bytes))
    return value.decode("ascii") if isinstance(value, bytes) else value
