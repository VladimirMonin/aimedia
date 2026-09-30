"""Безопасное скачивание remote image artifact Polza (E06, C09c2). 🔽

Принимает доменную `RemoteArtifact` и возвращает ограниченные сырые байты; ничего не
пишет на диск, не читает `.env`/окружение и не определяет MIME/контейнер (формат
проверяет downstream по байтам). Поддерживается только документированная Polza форма
доставки (`docs/Get Media.txt`) — абсолютный `https` URL на одобренном CDN-host;
inline base64 и `provider_file_id` закрываются отказом (fail closed).

Полный контракт границы — `instructions/PROVIDER.polza-media.instructions.md`.
Ключевое, что нельзя ослаблять:

- одобрен только документированный host `s3.polza.ai` (`docs/Get Media.txt`);
  неизвестный host, включая `cdn.polza.ai`, — отказ **до** DNS, без wildcard; только
  `https`/443, без userinfo/fragment/IP-literal/управляющих символов, с ограничением
  длины URL; подписанный query не попадает в диагностику;
- без bearer/cookie/proxy на CDN: proxy env не читается (`proxy=None`), редирект не
  отслеживается, автоматического повтора нет (`retries=0`), тело ограничено
  `max_artifact_bytes` до объединения, любое `Content-Encoding` кроме `identity`
  отклоняется до чтения, `Content-Type` — advisory, ответ закрывается на всех путях;
- почему собственный транспорт: проверка адреса до обычного клиента не защищает от
  DNS-rebinding. Downloader **сам** строит `httpcore.AsyncConnectionPool` над
  внутренним `DnsPinningBackend` (пул снаружи не инжектируется): резолвинг один раз в
  пределах конечного таймаута, все ответы обязаны быть глобально-публичными, прямому
  `AnyIOBackend` уходит числовой адрес. Исходный hostname остаётся в URL, поэтому TLS
  SNI и проверка сертификата идут по нему со строгим контекстом по умолчанию;
- `httpcore` на DEBUG пишет target с подписью и `Location` до отказа от редиректа,
  поэтому downloader держит безопасный уровень логгера `httpcore`
  (:func:`_harden_transport_logging`); это же скрывает `Authorization` gateway.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import socket
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Final
from urllib.parse import urlsplit

import httpcore

from aimedia.domain.artifacts import ArtifactKind, RemoteArtifact
from aimedia.domain.errors import JobError, ProviderError

ARTIFACT_DOWNLOAD_FAILED: Final = "ARTIFACT_DOWNLOAD_FAILED"
"""Стабильный код отказа скачивания (`docs/plans/04-cli-contract.md`, «Error codes»)."""

APPROVED_ARTIFACT_HOSTS: Final[frozenset[str]] = frozenset({"s3.polza.ai"})
"""Единственный host, подтверждённый документацией Polza (`docs/Get Media.txt`).

Wildcard и прочие CDN, включая `cdn.polza.ai`, запрещены: пока нет подтверждающего
документа, недокументированный host закрывается отказом до DNS.
"""

ARTIFACT_HTTPS_PORT: Final = 443
"""Единственный допустимый порт скачивания artifact."""

DEFAULT_DOWNLOAD_TIMEOUT_SECONDS: Final = 30.0
"""Конечный бюджет DNS+connect+read, если вызывающая сторона не задала свой."""

MAX_ARTIFACT_URL_LENGTH: Final = 8192
"""Верхняя граница длины URL до разбора (fail closed на аномально длинных входах)."""

HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]
"""Резолвер host → последовательность строковых адресов. Инжектируется для тестов."""

_REASON_UNSUPPORTED_KIND: Final = "unsupported_artifact_kind"
_REASON_UNSUPPORTED_DELIVERY: Final = "unsupported_delivery"
_REASON_MISSING_DELIVERY: Final = "missing_delivery"
_REASON_INSECURE_URL: Final = "insecure_url"
_REASON_UNAPPROVED_HOST: Final = "unapproved_host"
_REASON_UNRESOLVABLE_HOST: Final = "unresolvable_host"
_REASON_UNSAFE_ADDRESS: Final = "unsafe_address"
_REASON_REDIRECT: Final = "redirect"
_REASON_HTTP_ERROR: Final = "http_error"
_REASON_UNSUPPORTED_ENCODING: Final = "unsupported_content_encoding"
_REASON_INVALID_RESPONSE: Final = "invalid_response"
_REASON_TOO_LARGE: Final = "too_large"
_REASON_TIMEOUT: Final = "timeout"
_REASON_TRANSPORT_ERROR: Final = "transport_error"

_HTTPCORE_FAILURES: Final = (
    httpcore.TimeoutException,
    httpcore.NetworkError,
    httpcore.ProtocolError,
)
_HttpcoreFailure = httpcore.TimeoutException | httpcore.NetworkError | httpcore.ProtocolError

_MESSAGE: Final = "Не удалось безопасно получить remote artifact."
_TRANSPORT_LOGGER_NAME: Final = "httpcore"
_SAFE_TRANSPORT_LOG_LEVEL: Final = logging.WARNING
# h11 всё равно отклоняет более 20 цифр; здесь тот же предел защищает от ValueError
# встроенного лимита int() при чтении подставленного заголовка.
_MAX_CONTENT_LENGTH_DIGITS: Final = 20


class DnsPinningBackend(httpcore.AsyncNetworkBackend):
    """Сетевой backend, резолвящий host один раз и пинящий глобальный публичный IP.

    Оборачивает прямой backend (в production — `httpcore.AnyIOBackend`) и в
    `connect_tcp` подменяет host на проверенный числовой адрес. DNS-ожидание
    ограничено конечным бюджетом (`dns_timeout`, при необходимости урезанным
    connect-таймаутом httpcore), поэтому зависший резолвер не обходит таймаут. Любой
    приватный, специальный, IPv4-mapped или смешанный ответ закрывает соединение
    типизированной ошибкой до установки TCP. `connect_unix_socket` не поддерживается:
    uds-путь не должен обходить проверку.
    """

    def __init__(
        self,
        *,
        direct: httpcore.AsyncNetworkBackend,
        resolver: HostResolver,
        dns_timeout: float,
    ) -> None:
        self._direct = direct
        self._resolver = resolver
        self._dns_timeout = _require_positive_timeout(dns_timeout, "dns_timeout")

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned = await self._pin_address(host, port, timeout)
        return await self._direct.connect_tcp(
            pinned,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise _download_error(_REASON_UNSAFE_ADDRESS)

    async def sleep(self, seconds: float) -> None:
        await self._direct.sleep(seconds)

    async def _pin_address(self, host: str, port: int, timeout: float | None) -> str:
        """Резолвить host ровно один раз в пределах конечного бюджета.

        Если резолвер не вернул ни одного адреса или хотя бы один ответ не является
        глобально-публичным, соединение не устанавливается. Зависший резолвер
        обрывается по `dns_timeout`/connect-таймауту и даёт типизированный отказ без
        имени host и без исходного текста.
        """
        budget = self._dns_timeout if timeout is None else min(timeout, self._dns_timeout)
        addresses: Sequence[str] = ()
        failure: str | None = None
        try:
            addresses = await asyncio.wait_for(self._resolver(host, port), budget)
        except TimeoutError:
            # Сначала TimeoutError: в Python 3.11+ он подкласс OSError.
            failure = _REASON_TIMEOUT
        except OSError:
            failure = _REASON_UNRESOLVABLE_HOST
        if failure is not None:
            raise _download_error(failure, retryable=failure == _REASON_TIMEOUT)
        if not addresses:
            raise _download_error(_REASON_UNRESOLVABLE_HOST)
        chosen: str | None = None
        for candidate in addresses:
            if not _is_global_public_address(candidate):
                raise _download_error(_REASON_UNSAFE_ADDRESS)
            if chosen is None:
                chosen = candidate
        if chosen is None:  # pragma: no cover - недостижимо при непустом списке
            raise _download_error(_REASON_UNRESOLVABLE_HOST)
        return chosen


class PolzaArtifactDownloader:
    """Скачивание image artifact Polza в ограниченные байты.

    Downloader **сам** строит проверяющий пул соединений: снаружи нельзя передать
    произвольный `httpcore.AsyncConnectionPool`, поэтому DNS-закрепление нельзя
    случайно обойти. ``resolver``/``direct_backend`` — инжектируемые швы для offline
    тестов; ``max_artifact_bytes`` — локальный safety-лимит на сырые байты (до
    объединения и декомпрессии); ``timeout_seconds`` — конечный бюджет DNS, connect и
    чтения (по умолчанию :data:`DEFAULT_DOWNLOAD_TIMEOUT_SECONDS`). Политика повторов
    остаётся за вызывающей стороной — сам downloader не повторяет запрос.
    """

    def __init__(
        self,
        *,
        max_artifact_bytes: int,
        timeout_seconds: float | None = None,
        resolver: HostResolver | None = None,
        direct_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        if (
            isinstance(max_artifact_bytes, bool)
            or not isinstance(max_artifact_bytes, int)
            or max_artifact_bytes <= 0
        ):
            raise ValueError("max_artifact_bytes должен быть положительным safety-лимитом")
        timeout = (
            DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
            if timeout_seconds is None
            else _require_positive_timeout(timeout_seconds, "timeout_seconds")
        )
        self._max_artifact_bytes = max_artifact_bytes
        self._timeout_seconds = timeout
        resolved_resolver = resolver if resolver is not None else resolve_public_addresses
        resolved_backend = direct_backend if direct_backend is not None else httpcore.AnyIOBackend()
        self._pool = _build_pinned_pool(
            resolver=resolved_resolver,
            direct_backend=resolved_backend,
            dns_timeout=timeout,
        )
        _harden_transport_logging()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(max_artifact_bytes={self._max_artifact_bytes})"

    async def fetch(self, artifact: RemoteArtifact) -> bytes:
        """Вернуть ограниченные сырые байты image artifact по документированному URL.

        Поддерживается ровно документированная Polza форма доставки — абсолютный
        `https` URL одобренного CDN. Inline base64 и `provider_file_id` не
        поддерживаются и закрываются отказом. MIME и контейнер здесь не проверяются.
        """
        if artifact.kind is not ArtifactKind.IMAGE:
            raise _download_error(_REASON_UNSUPPORTED_KIND)
        if artifact.provider_file_id is not None or artifact.base64_data is not None:
            raise _download_error(_REASON_UNSUPPORTED_DELIVERY)
        url = artifact.url
        if url is None:
            raise _download_error(_REASON_MISSING_DELIVERY)
        return await self._download(url)

    async def aclose(self) -> None:
        """Закрыть внутренний пул соединений."""
        await self._pool.aclose()

    async def _download(self, raw_url: str) -> bytes:
        """Выполнить один ограниченный потоковый GET по одобренному URL."""
        url = _approved_download_url(raw_url)
        request = httpcore.Request(
            "GET",
            url,
            headers=[
                ("host", url.host.decode("ascii")),
                ("accept-encoding", "identity"),
                ("accept", "image/*"),
            ],
            extensions=_request_extensions(self._timeout_seconds),
        )
        transport_failure: _HttpcoreFailure | None = None
        response: httpcore.Response | None = None
        try:
            response = await self._pool.handle_async_request(request)
        except _HTTPCORE_FAILURES as exc:
            transport_failure = exc
        if transport_failure is not None:
            # Ошибка строится вне блока `except`: исходный текст (в том числе
            # подписанный URL или сырые байты ответа) не попадает в traceback/__context__.
            raise _failure_error(transport_failure)
        assert response is not None
        return await self._consume(response)

    async def _consume(self, response: httpcore.Response) -> bytes:
        """Прочитать потоковое тело с safety-cap, закрыв ответ на любом пути.

        Разбор всех заголовков (кодирование, длина) выполняется внутри `try/finally`,
        поэтому даже враждебный `Content-Length` не оставит ответ незакрытым.
        """
        status = response.status
        unsupported_encoding = False
        oversized = False
        chunks: list[bytes] = []
        size = 0
        failure: _HttpcoreFailure | None = None
        try:
            unsupported_encoding = _content_encoding_unsupported(response.headers)
            declared = _content_length(response.headers)
            if (
                not unsupported_encoding
                and declared is not None
                and declared > self._max_artifact_bytes
            ):
                oversized = True
            if not unsupported_encoding and not oversized:
                async for chunk in response.aiter_stream():
                    size += len(chunk)
                    if size > self._max_artifact_bytes:
                        oversized = True
                        break
                    chunks.append(chunk)
        except _HTTPCORE_FAILURES as exc:
            failure = exc
        finally:
            # Ответ закрывается на успехе, отказе и отмене: соединение не остаётся
            # арендованным. `httpcore.PoolByteStream.aclose` защищён от отмены.
            await response.aclose()
        if failure is not None:
            raise _failure_error(failure)
        if unsupported_encoding:
            raise _download_error(_REASON_UNSUPPORTED_ENCODING)
        if oversized:
            raise _download_error(
                _REASON_TOO_LARGE, details={"max_artifact_bytes": self._max_artifact_bytes}
            )
        if 300 <= status < 400:
            # httpcore не следует редиректам; 3xx всегда отказ, включая тот же host.
            raise _download_error(_REASON_REDIRECT, details={"http_status": status})
        if not 200 <= status < 300:
            raise _download_error(
                _REASON_HTTP_ERROR,
                details={"http_status": status},
                retryable=_http_retryable(status),
            )
        return b"".join(chunks)


def _build_pinned_pool(
    *,
    resolver: HostResolver,
    direct_backend: httpcore.AsyncNetworkBackend,
    dns_timeout: float,
) -> httpcore.AsyncConnectionPool:
    """Собрать пул, единственный backend которого — проверяющий `DnsPinningBackend`.

    `proxy=None` и `uds=None` явно документируют прямое подключение без env-proxy и
    unix-socket. `retries=0` исключает автоматический повтор. `ssl_context=None`
    оставляет строгий контекст по умолчанию (`httpcore.default_ssl_context`).
    """
    backend = DnsPinningBackend(
        direct=direct_backend,
        resolver=resolver,
        dns_timeout=dns_timeout,
    )
    return httpcore.AsyncConnectionPool(
        proxy=None,
        retries=0,
        uds=None,
        network_backend=backend,
    )


def _harden_transport_logging() -> None:
    """Не позволять сырому httpcore-транспорту логировать URL и заголовки.

    На DEBUG `httpcore` пишет request target (подписанный query) и сырые заголовки
    ответа (`Location` с подписью) до проверки downloader. Уровень WARNING на
    родительском логгере `httpcore` наследуется дочерними (`httpcore.http11` и др.) и
    выключает эти записи даже при root DEBUG; это же скрывает `Authorization` gateway,
    ходящего через `httpcore`. Явное понижение уровня `httpcore.*` возвращает утечку.
    """
    logger = logging.getLogger(_TRANSPORT_LOGGER_NAME)
    if logger.level < _SAFE_TRANSPORT_LOG_LEVEL:
        logger.setLevel(_SAFE_TRANSPORT_LOG_LEVEL)


async def resolve_public_addresses(host: str, port: int) -> Sequence[str]:
    """Разрешить host через системный resolver asyncio, не отбрасывая ответы.

    Возвращает адреса в порядке ответа без дедупликации по семейству. Решение о
    публичности принимает `DnsPinningBackend`, а не резолвер: один провайдер
    резолвинга не должен молча фильтровать ответы.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        candidate = info[4][0]
        if isinstance(candidate, str) and candidate not in addresses:
            addresses.append(candidate)
    return tuple(addresses)


def _request_extensions(timeout_seconds: float) -> dict[str, object]:
    """Применить конечный бюджет таймаута к каждому этапу запроса."""
    return {
        "timeout": {
            "connect": timeout_seconds,
            "read": timeout_seconds,
            "write": timeout_seconds,
            "pool": timeout_seconds,
        }
    }


def _approved_download_url(raw: str) -> httpcore.URL:
    """Проверить URL и собрать нормализованный `httpcore.URL` одобренного origin.

    Возвращаемый URL строится из проверенных частей с пониженным регистром host,
    поэтому и HTTP `Host`, и TLS SNI, и проверка сертификата идут по точному
    одобренному hostname, независимо от регистра и исходной записи.
    """
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > MAX_ARTIFACT_URL_LENGTH
        or not raw.isascii()
    ):
        raise _download_error(_REASON_INSECURE_URL)
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F or char == "\\" for char in raw):
        raise _download_error(_REASON_INSECURE_URL)
    parsed = None
    port: int | None = None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        parsed = None
    if parsed is None:
        raise _download_error(_REASON_INSECURE_URL)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment != ""
        or port not in (None, ARTIFACT_HTTPS_PORT)
        or _is_ip_literal(host)
    ):
        raise _download_error(_REASON_INSECURE_URL)
    lowered = host.lower()
    if lowered not in APPROVED_ARTIFACT_HOSTS:
        raise _download_error(_REASON_UNAPPROVED_HOST)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return httpcore.URL(
        scheme="https",
        host=lowered.encode("ascii"),
        port=ARTIFACT_HTTPS_PORT,
        target=target,
    )


def _is_global_public_address(address: str) -> bool:
    """Является ли строковый адрес глобально-публичным (fail closed на всём прочем).

    Отдельно отклоняются IPv4-mapped IPv6 (обход IPv4-проверок), multicast (Python
    считает `ff02::1` глобальным), reserved и служебные диапазоны вроде CGNAT
    `100.64.0.0/10`, у которых `is_private` ложно, но `is_global` тоже ложно.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return ip.is_global


def _is_ip_literal(host: str) -> bool:
    """Отличить IP-literal от hostname, чтобы не принимать числовой host за домен."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _header_values(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    """Все значения заголовка (по каждому вхождению) без учёта регистра имени."""
    lowered = name.lower()
    return [value for key, value in headers if key.lower() == lowered]


def _content_encoding_unsupported(headers: Sequence[tuple[bytes, bytes]]) -> bool:
    """Есть ли хоть одно значение `Content-Encoding`, отличное от `identity`.

    Проверяются **все** вхождения и все comma-разделённые токены: частично
    сжатое или неоднозначное тело не должно читаться как identity.
    """
    for raw in _header_values(headers, b"content-encoding"):
        for token in raw.decode("ascii", "replace").split(","):
            if token.strip().lower() not in ("", "identity"):
                return True
    return False


def _content_length(headers: Sequence[tuple[bytes, bytes]]) -> int | None:
    """Объявленная длина тела, если она однозначна и безопасно разбирается.

    Несколько значений, не-ASCII цифры или токен длиннее предела (`h11` тоже
    отклоняет >20 цифр) трактуются как отсутствие подсказки: решение принимает
    потоковый cap, а не потенциально взрывоопасный `int()`.
    """
    values = _header_values(headers, b"content-length")
    if len(values) != 1:
        return None
    token = values[0].strip()
    if not token.isascii() or not token.isdigit() or len(token) > _MAX_CONTENT_LENGTH_DIGITS:
        return None
    return int(token)


def _http_retryable(status: int) -> bool:
    """Транзиентные HTTP-статусы, при которых явный повтор GET имеет смысл."""
    return status in (408, 429) or 500 <= status < 600


def _require_positive_timeout(value: object, name: str) -> float:
    """Потребовать конечный положительный таймаут; отклонить NaN/inf/bool."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} должен быть конечным положительным числом")
    return float(value)


def _failure_error(failure: _HttpcoreFailure) -> ProviderError:
    """Типизировать транспортный/протокольный сбой без текста и сырых байт.

    Некорректный ответ CDN (`ProtocolError`) мог бы нести в сообщении сырые байты,
    поэтому он так же превращается в фиксированный отказ вне исходного контекста.
    """
    if isinstance(failure, httpcore.TimeoutException):
        return _download_error(_REASON_TIMEOUT, retryable=True)
    if isinstance(failure, httpcore.ProtocolError):
        return _download_error(_REASON_INVALID_RESPONSE)
    return _download_error(_REASON_TRANSPORT_ERROR)


def _download_error(
    reason: str,
    *,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> ProviderError:
    """Построить типизированный отказ скачивания без URL, query и сырых байтов.

    `reason` — машинный токен из фиксированного набора вызовов, `details` — только
    безопасные числа/статусы. Сообщение фиксировано; подписанный URL никогда не
    попадает ни в текст, ни в `details`, ни в контекст исключения.
    """
    merged: dict[str, object] = {"reason": reason}
    if details is not None:
        merged.update(details)
    return ProviderError(
        JobError(
            code=ARTIFACT_DOWNLOAD_FAILED,
            message=_MESSAGE,
            retryable=retryable,
            details=merged,
        )
    )


__all__ = [
    "APPROVED_ARTIFACT_HOSTS",
    "ARTIFACT_DOWNLOAD_FAILED",
    "ARTIFACT_HTTPS_PORT",
    "DEFAULT_DOWNLOAD_TIMEOUT_SECONDS",
    "MAX_ARTIFACT_URL_LENGTH",
    "DnsPinningBackend",
    "PolzaArtifactDownloader",
    "resolve_public_addresses",
]
