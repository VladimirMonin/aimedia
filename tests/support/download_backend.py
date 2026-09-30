"""Управляемые offline-двойники сети для тестов скачивания artifact (C09c2).

Модуль живёт в `tests/support` и **не является** production кодом: его не
импортирует ни один модуль `aimedia`. Каталог добавляется в `sys.path` только из
`tests/conftest.py`.

Двойники детерминированы и не открывают сокетов: `RecordingBackend` отдаёт заранее
собранные байты HTTP-ответа, `FakeResolver` возвращает заданные адреса, может
«зависнуть» или упасть, а `ScriptedResponseStream` позволяет напрямую проверить
закрытие ответа downloader-ом. Это покрывает реальный `httpcore`-конвейер
(сериализацию запроса, разбор ответа, TLS-параметры, закрытие) без сети.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator, Iterable, Sequence

import httpcore

PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"

_HTTP_REASONS = {
    200: "OK",
    204: "No Content",
    206: "Partial Content",
    301: "Moved Permanently",
    302: "Found",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def http_head(
    status: int = 200,
    *,
    headers: Sequence[tuple[bytes, bytes]] = (),
    content_length: int | None = None,
) -> bytes:
    """Собрать блок HTTP/1.1-заголовков с корректным Content-Length."""
    lines = [f"HTTP/1.1 {status} {_HTTP_REASONS.get(status, 'OK')}".encode("ascii")]
    names = {name.lower() for name, _ in headers}
    if b"content-length" not in names and b"transfer-encoding" not in names:
        length = 0 if content_length is None else content_length
        lines.append(b"content-length: " + str(length).encode("ascii"))
    lines.extend(name + b": " + value for name, value in headers)
    lines.append(b"")
    lines.append(b"")
    return b"\r\n".join(lines)


def raw_http_response(
    status: int = 200,
    body: bytes = b"",
    *,
    headers: Sequence[tuple[bytes, bytes]] = (),
    content_length: int | None = None,
) -> bytes:
    """Собрать полный сырой HTTP-ответ (заголовки + тело) как один чанк."""
    if content_length is None:
        content_length = len(body)
    return http_head(status, headers=headers, content_length=content_length) + body


def chunked_head(status: int = 200, *, headers: Sequence[tuple[bytes, bytes]] = ()) -> bytes:
    """Блок заголовков для ответа с `transfer-encoding: chunked` (нет Content-Length)."""
    return http_head(status, headers=[*headers, (b"transfer-encoding", b"chunked")])


def chunked_body(data: bytes) -> bytes:
    """Один chunked-чанк (без завершающего нулевого чанка)."""
    return f"{len(data):X}".encode("ascii") + b"\r\n" + data + b"\r\n"


class FakeResolver:
    """Резолвер host → адреса с записью вызовов, управляемым сбоем и зависанием."""

    def __init__(
        self,
        answers: dict[str, Sequence[str]] | None = None,
        *,
        default: Sequence[str] = (),
        failure: OSError | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self.answers = {host: tuple(addresses) for host, addresses in (answers or {}).items()}
        self.default = tuple(default)
        self.failure = failure
        self.hang_seconds = hang_seconds
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        self.calls.append((host, port))
        if self.hang_seconds is not None:
            await asyncio.sleep(self.hang_seconds)
        if self.failure is not None:
            raise self.failure
        return self.answers.get(host, self.default)


class RecordingStream(httpcore.AsyncNetworkStream):
    """Async-поток, отдающий заранее собранные чанки и записывающий активность.

    Хранит отправленные байты запроса, параметры `start_tls` и факт закрытия.
    `read_error` поднимается один раз после исчерпания чанков — так моделируется
    прерванное чтение тела (сетевой сбой или отмена) без ожидания.
    """

    def __init__(self, chunks: Iterable[bytes], *, read_error: BaseException | None = None) -> None:
        self._pending = list(chunks)
        self._read_error = read_error
        self.served: list[bytes] = []
        self.request_bytes = b""
        self.tls: list[tuple[ssl.SSLContext, str | None]] = []
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._pending:
            chunk = self._pending.pop(0)
            self.served.append(chunk)
            return chunk
        if self._read_error is not None:
            error, self._read_error = self._read_error, None
            raise error
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.request_bytes += buffer

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.tls.append((ssl_context, server_hostname))
        return self

    def get_extra_info(self, info: str) -> object:
        return None


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Прямой backend, отдающий scripted-ответ и записывающий `connect_tcp(host)`."""

    def __init__(
        self,
        chunks: Iterable[bytes],
        *,
        read_error: BaseException | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self._chunks = tuple(chunks)
        self._read_error = read_error
        self._connect_error = connect_error
        self.connects: list[tuple[str, int]] = []
        self.streams: list[RecordingStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connects.append((host, port))
        if self._connect_error is not None:
            raise self._connect_error
        stream = RecordingStream(self._chunks, read_error=self._read_error)
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("unix-socket не должен использоваться при скачивании artifact")

    async def sleep(self, seconds: float) -> None:
        return None


class ScriptedResponseStream:
    """Тело ответа для прямого вызова `_consume`: scripted-чанки + флаг закрытия."""

    def __init__(self, chunks: Iterable[bytes] = (), *, error: BaseException | None = None) -> None:
        self._chunks = tuple(chunks)
        self._error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    async def aclose(self) -> None:
        self.closed = True


def response_with(
    stream: ScriptedResponseStream,
    *,
    status: int = 200,
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> httpcore.Response:
    """Собрать `httpcore.Response` поверх scripted-тела для проверки `_consume`."""
    return httpcore.Response(status, headers=list(headers), content=stream)
