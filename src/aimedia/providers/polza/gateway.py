"""HTTP-шлюз Polza Media: submit, status и результат (E06, C09c1).

Шлюз реализует доменные порты `ProviderGateway` и `PollingProviderGateway`
(`docs/plans/05-provider-system.md`, «Provider client»): принимает доменный
`ImageGenerationRequest`, строит чистый payload через media mapper, отправляет
`POST /v1/media` один раз и нормализует ответ; затем читает `GET /v1/media/{id}`.

Границы среза C09c1:

- шлюз **не** скачивает remote artifact. `RemoteArtifact` остаётся ссылкой, а
  загрузка/декодирование/конвертация — отдельный срез C09c2;
- шлюз **не** создаёт `httpx.AsyncClient`: клиент инжектируется вызывающей
  стороной и переиспользуется всеми вызовами (никакого клиента на каждый polling);
- ключ API инжектируется явно и живёт только в памяти. Шлюз не читает `.env`,
  переменные окружения, CLI-флаги или БД.

Безопасность транспорта:

- база зафиксирована как `https://polza.ai/api/v1`, а не берётся из запроса или
  непроверенного источника. Authorization добавляется только на этот origin;
- `follow_redirects=False` задаётся на каждом запросе, даже если клиент
  сконфигурирован иначе: редирект отклоняется типизированной ошибкой и не
  передаёт ключ на чужой host;
- `remote_job_id` независимо валидируется как безопасный односегментный ID и
  percent-кодируется при построении URL статуса. Скрытого busy-polling нет:
  каждый вызов выполняет ровно один HTTP-запрос;
- тело ответа читается потоково до локального safety-cap, затем разбирается JSON;
  если транспорт уже вернул буферизованный ответ (`response.is_stream_consumed`),
  повторная итерация потока невозможна (`httpx.StreamConsumed` не является
  `httpx.HTTPError`), поэтому используется уже прочитанный буфер, а safety-cap
  применяется к нему до разбора JSON. Ошибки типизированы, а
  `str`/`details`/traceback не содержат сырого тела ответа, URL, query, payload
  запроса или текста исключения. Если `httpx`-исключение несёт подписанный URL,
  типизированная ошибка строится вне блока `except`, поэтому `__cause__`/`__context__`
  пусты;
- автоматического повторного `POST` нет: неоднозначный исход оплаченного submit
  (timeout/disconnect, HTTP 408, 5xx, нечитаемое или превысившее safety-лимит тело,
  2xx без пригодного конверта) даёт `SUBMIT_UNCERTAIN` с `retryable=None`, а не
  второй submit. Явные отказы остаются различимыми: 400/401/402/403, а 429 — это
  отказ до обработки (не неизвестный исход). Известный терминальный `failed` с
  валидным ID не маскируется под неоднозначность и остаётся
  `REMOTE_GENERATION_FAILED`; сетевой timeout не маскируется под «недостаточно
  средств»;
- ответы разбираются только через `decode_media_json` (точный `Decimal`) и
  `normalize_media_*`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Final
from urllib.parse import quote

import httpx

from aimedia.domain.errors import (
    InputFileNotFoundError,
    InvalidParameterValueError,
    JobError,
    ProviderError,
    UnknownProviderError,
)
from aimedia.domain.inputs import InputRef
from aimedia.domain.ports import ProviderResult, SubmissionResult
from aimedia.domain.refs import (
    ProviderCapabilities,
    ProviderJobState,
    RemoteJobRef,
    RemoteOperation,
)
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.providers.polza.media.request import (
    build_media_request,
    serialize_media_request,
)
from aimedia.providers.polza.media.response import (
    POLZA_PROVIDER_ID,
    decode_media_json,
    normalize_media_result,
    normalize_media_status,
    normalize_media_submission,
    validate_media_response_id,
)
from aimedia.registry.models import EffectiveModelDefinition
from aimedia.registry.validator import validate_model_request

POLZA_API_BASE_URL: Final = "https://polza.ai/api/v1"
"""Зафиксированный production base URL; не берётся из запроса или конфигурации клиента."""

SUBMIT_UNCERTAIN: Final = "SUBMIT_UNCERTAIN"
"""Исход POST с неизвестным результатом: автоматический повтор запрещён."""

_PROVIDER_AUTHENTICATION: Final = "PROVIDER_AUTHENTICATION"
_PROVIDER_INSUFFICIENT_BALANCE: Final = "PROVIDER_INSUFFICIENT_BALANCE"
_PROVIDER_FORBIDDEN: Final = "PROVIDER_FORBIDDEN"
_PROVIDER_RATE_LIMIT: Final = "PROVIDER_RATE_LIMIT"
_PROVIDER_TIMEOUT: Final = "PROVIDER_TIMEOUT"
_PROVIDER_UNAVAILABLE: Final = "PROVIDER_UNAVAILABLE"
_PROVIDER_HTTP_ERROR: Final = "PROVIDER_HTTP_ERROR"
_PROVIDER_REDIRECT: Final = "PROVIDER_REDIRECT"
_PROVIDER_RESPONSE_TOO_LARGE: Final = "PROVIDER_RESPONSE_TOO_LARGE"
_PROVIDER_TRANSPORT_ERROR: Final = "PROVIDER_TRANSPORT_ERROR"
_PROVIDER_INVALID_REMOTE_REF: Final = "PROVIDER_INVALID_REMOTE_REF"
# Код нормализованного некорректного ответа. Для 2xx-submit его наличие означает
# неоднозначный исход (см. `_json_request`/`submit`), для GET — просто некорректный
# ответ, который не превращается в `SUBMIT_UNCERTAIN`.
_PROVIDER_INVALID_RESPONSE: Final = "PROVIDER_INVALID_RESPONSE"

# Сентинел 2xx-тела submit, которое не разбирается как media-JSON.
_UNPARSEABLE_BODY: Final = object()

# Безопасный односегментный ID: буквы/цифры, дефис и подчёркивание, не длиннее 128.
# Совпадает по строгости с проверкой ответа, но определяется независимо, чтобы шлюз
# не полагался на доверие уже разобранному значению.
_SAFE_REMOTE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
# Разрешённый trace ID из error-конверта: короткий ASCII machine-токен, не текст.
_SAFE_TRACE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

_MEDIA_SEGMENT: Final = "/media"
_STATUS_ERROR_CODES: Final[dict[int, tuple[str, bool | None]]] = {
    401: (_PROVIDER_AUTHENTICATION, False),
    402: (_PROVIDER_INSUFFICIENT_BALANCE, False),
    403: (_PROVIDER_FORBIDDEN, False),
    408: (_PROVIDER_TIMEOUT, True),
    429: (_PROVIDER_RATE_LIMIT, True),
}


class PolzaProviderGateway:
    """Адаптер Polza Media, реализующий `ProviderGateway` и `PollingProviderGateway`.

    `effective` — уже разрешённая effective definition (Model Registry), несущая
    `provider_id` и `remote_model_id`. `submit` повторно проверяет binding через
    `validate_model_request`, поэтому запрос с чужой моделью/provider завершается
    типизированной доменной ошибкой до любого HTTP-вызова.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
        effective: EffectiveModelDefinition,
        max_body_bytes: int,
        max_response_bytes: int,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key должен быть непустой строкой")
        _require_positive_local_cap(max_body_bytes, "max_body_bytes")
        _require_positive_local_cap(max_response_bytes, "max_response_bytes")
        self._client = client
        self._api_key = api_key
        self._effective = effective
        self._max_body_bytes = max_body_bytes
        self._max_response_bytes = max_response_bytes

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider_id={POLZA_PROVIDER_ID!r})"

    @property
    def provider_id(self) -> str:
        return POLZA_PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(async_jobs=True, polling=True, cancellation=False)

    @property
    def client(self) -> httpx.AsyncClient:
        """Инжектированный клиент, переиспользуемый всеми вызовами шлюза."""
        return self._client

    async def submit(self, request: ImageGenerationRequest) -> SubmissionResult:
        """Проверить binding, построить payload и один раз отправить `POST /media`.

        Любой ответ, который не доказывает непринятие оплаченного запроса
        (транспортный сбой, HTTP 408/5xx, нечитаемое или превысившее safety-лимит
        тело, 2xx без пригодного конверта), даёт `SUBMIT_UNCERTAIN` с запретом
        повторного submit: provider мог принять задание. Известный терминальный
        `failed` с валидным ID не маскируется.
        """
        if self._effective.provider_id != POLZA_PROVIDER_ID:
            raise UnknownProviderError(
                "Шлюз Polza не обслуживает выбранный provider.",
                details={"provider": self._effective.provider_id},
            )
        validated = validate_model_request(self._effective, request)
        reference_bytes = _read_reference_bytes(request.images, self._max_body_bytes)
        payload = build_media_request(
            validated,
            reference_bytes,
            max_body_bytes=self._max_body_bytes,
        )
        body = serialize_media_request(payload)
        decoded = await self._json_request("POST", _media_url(), content=body, uncertain=True)
        # 2xx без пригодного конверта (нет безопасного remote job ID, неизвестный
        # `object`/`status`) оставляет исход submit неизвестным и запрещает вто-
        # рой POST. Ошибка строится вне блока `except`, поэтому контекст пуст.
        submission: SubmissionResult | None = None
        try:
            submission = normalize_media_submission(decoded)
        except ProviderError as exc:
            if exc.error.code != _PROVIDER_INVALID_RESPONSE:
                raise
        if submission is None:
            raise self._submit_uncertain()
        return submission

    async def get_status(self, remote_ref: RemoteJobRef) -> ProviderJobState:
        """Один `GET /media/{id}` и нормализация состояния; без скрытого polling."""
        decoded = await self._json_request(
            "GET", _status_url(remote_ref), content=None, uncertain=False
        )
        validate_media_response_id(decoded, remote_ref.remote_job_id)
        return normalize_media_status(decoded)

    async def fetch_result(self, remote_ref: RemoteJobRef) -> ProviderResult:
        """Один `GET /media/{id}` и нормализация завершённого результата."""
        decoded = await self._json_request(
            "GET", _status_url(remote_ref), content=None, uncertain=False
        )
        validate_media_response_id(decoded, remote_ref.remote_job_id)
        return normalize_media_result(decoded)

    async def _json_request(
        self,
        method: str,
        url: str,
        *,
        content: bytes | None,
        uncertain: bool,
    ) -> object:
        """Выполнить один HTTP-запрос и вернуть разобранный JSON.

        Ключ уходит только на зафиксированный origin `polza.ai`; редирект не
        отслеживается даже при `follow_redirects=True` у клиента. Поток ответа
        закрывается при превышении лимита, ошибке и отмене. Транспортный сбой
        превращается в типизированную ошибку вне блока `except`, поэтому исходное
        исключение (в том числе с подписанным URL) не попадает в traceback.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if content is not None:
            headers["Content-Type"] = "application/json"

        transport_failure: httpx.HTTPError | None = None
        oversized = False
        unsupported_encoding = False
        status = 0
        body_parts: list[bytes] = []
        body_size = 0
        try:
            async with self._client.stream(
                method,
                url,
                headers=headers,
                content=content,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if (
                    response.headers.get("content-encoding", "identity").strip().lower()
                    != "identity"
                ):
                    unsupported_encoding = True
                elif response.is_stream_consumed:
                    # Транспорт уже отдал тело целиком (`Response(content=...)`
                    # вызывает `read()`), поэтому поток повторно не итерируется:
                    # `httpx.StreamConsumed` — это `RuntimeError`, а не `HTTPError`.
                    # Берём готовый буфер и применяем тот же safety-cap до JSON.
                    buffered = response.content
                    if len(buffered) > self._max_response_bytes:
                        oversized = True
                    else:
                        body_parts.append(buffered)
                else:
                    async for chunk in response.aiter_raw(
                        chunk_size=min(64 * 1024, self._max_response_bytes + 1)
                    ):
                        body_size += len(chunk)
                        if body_size > self._max_response_bytes:
                            oversized = True
                            break
                        body_parts.append(chunk)
        except httpx.HTTPError as exc:
            transport_failure = exc
        if transport_failure is not None:
            raise self._transport_error(transport_failure, uncertain=uncertain)
        if unsupported_encoding:
            # Тело нечитаемо: для submit нельзя доказать, что задание не принято.
            if uncertain:
                raise self._submit_uncertain()
            raise self._unsupported_encoding()
        if oversized:
            if uncertain:
                raise self._submit_uncertain()
            raise self._response_too_large()
        raw_body = b"".join(body_parts)
        if 300 <= status < 400:
            raise self._redirect_error(status)
        if not 200 <= status < 300:
            raise self._http_error(status, _extract_trace_id(raw_body), uncertain=uncertain)
        if uncertain:
            # 2xx с неразбираемым телом: submit мог быть принят, поэтому исход
            # неоднозначен и повторный POST запрещён. Новая ошибка строится вне
            # блока `except`, поэтому `__cause__`/`__context__` остаются пустыми.
            decoded: object = _UNPARSEABLE_BODY
            try:
                decoded = decode_media_json(raw_body)
            except ProviderError:
                pass
            if decoded is _UNPARSEABLE_BODY:
                raise self._submit_uncertain()
            return decoded
        return decode_media_json(raw_body)

    def _submit_uncertain(self) -> ProviderError:
        """Неоднозначный исход submit: повтор запрещён, тело/ID/URL не раскрываются.

        Используется для каждого пути, где оплаченный POST мог быть принят:
        транспортный сбой, HTTP 408/5xx, нечитаемое или превысившее safety-лимит
        тело, 2xx без пригодного конверта. `details` фиксированы и не несут
        provider-данных; `retryable=None` запрещает неявный автоматический повтор.
        """
        return ProviderError(
            JobError(
                code=SUBMIT_UNCERTAIN,
                message=(
                    "Исход отправки задания неизвестен; автоматический повтор "
                    "submit запрещён, требуется явное решение."
                ),
                retryable=None,
                details={"operation": "submit"},
            )
        )

    def _transport_error(self, exc: httpx.HTTPError, *, uncertain: bool) -> ProviderError:
        """Типизировать транспортный сбой без текста исключения и URL."""
        if uncertain:
            return self._submit_uncertain()
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError(
                JobError(
                    code=_PROVIDER_TIMEOUT,
                    message="Истекло время ожидания ответа provider.",
                    retryable=True,
                    details={"operation": "status"},
                )
            )
        return ProviderError(
            JobError(
                code=_PROVIDER_TRANSPORT_ERROR,
                message="Не удалось выполнить сетевой запрос к provider.",
                retryable=None,
                details={"operation": "status"},
            )
        )

    def _http_error(self, status: int, trace_id: str | None, *, uncertain: bool) -> ProviderError:
        """Типизировать HTTP-ошибку; для submit неоднозначные статусы → SUBMIT_UNCERTAIN.

        408 и 5xx не доказывают, что задание не принято (сервер мог обработать
        оплаченный запрос до таймаута или сбоя), поэтому повтор POST запрещён.
        Явные отказы остаются различимыми: 400/401/402/403, а 429 — это отказ до
        обработки, а не неизвестный исход.
        """
        if uncertain and (status == 408 or 500 <= status < 600):
            return self._submit_uncertain()
        code, retryable = _status_error_code(status)
        details: dict[str, object] = {"http_status": status}
        if trace_id is not None:
            details["trace_id"] = trace_id
        return ProviderError(
            JobError(
                code=code,
                message="Provider вернул ошибку HTTP.",
                retryable=retryable,
                details=details,
            )
        )

    def _redirect_error(self, status: int) -> ProviderError:
        return ProviderError(
            JobError(
                code=_PROVIDER_REDIRECT,
                message="Редирект provider отклонён без передачи Authorization.",
                retryable=False,
                details={"http_status": status},
            )
        )

    def _unsupported_encoding(self) -> ProviderError:
        return ProviderError(
            JobError(
                code=_PROVIDER_INVALID_RESPONSE,
                message="Неподдерживаемое кодирование ответа provider.",
                details={"reason": "unsupported_content_encoding"},
            )
        )

    def _response_too_large(self) -> ProviderError:
        return ProviderError(
            JobError(
                code=_PROVIDER_RESPONSE_TOO_LARGE,
                message="Ответ provider превышает локальный safety-лимит.",
                retryable=None,
                details={"max_response_bytes": self._max_response_bytes},
            )
        )


def _require_positive_local_cap(value: int, name: str) -> None:
    """Потребовать положительный целочисленный локальный safety-cap."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} должен быть положительным локальным safety-лимитом")


def _media_url() -> str:
    """URL создания media-задания на зафиксированном base."""
    return f"{POLZA_API_BASE_URL}{_MEDIA_SEGMENT}"


def _status_url(remote_ref: RemoteJobRef) -> str:
    """Построить URL статуса, независимо проверив и закодировав ID.

    Небезопасный или чужой ref отклоняется до HTTP: строка ID не угадывает
    endpoint и не превращается в часть пути без проверки.
    """
    if (
        remote_ref.provider_id != POLZA_PROVIDER_ID
        or remote_ref.operation is not RemoteOperation.MEDIA
        or _SAFE_REMOTE_ID.fullmatch(remote_ref.remote_job_id) is None
    ):
        raise ProviderError(
            JobError(
                code=_PROVIDER_INVALID_REMOTE_REF,
                message="Некорректная ссылка на удалённое media-задание.",
                details={"reason": "invalid_remote_job_id"},
            )
        )
    encoded = quote(remote_ref.remote_job_id, safe="")
    return f"{POLZA_API_BASE_URL}{_MEDIA_SEGMENT}/{encoded}"


def _read_reference_bytes(refs: Sequence[InputRef], max_body_bytes: int) -> list[bytes]:
    """Читать снимки с общим бюджетом не более max_body_bytes + 1 байт."""
    result: list[bytes] = []
    remaining = max_body_bytes
    for position, ref in enumerate(refs):
        content = _read_reference(ref, position, remaining, max_body_bytes)
        result.append(content)
        remaining -= len(content)
    return result


def _read_reference(ref: InputRef, position: int, remaining: int, max_body_bytes: int) -> bytes:
    """Прочитать максимум остаток бюджета + 1, не доверяя размеру InputRef/stat.

    Размер, MIME и SHA-256 проверяет mapper по фактическим байтам. Ошибки файловой
    системы отделены от доменной ошибки вне except, чтобы не раскрывать путь.
    """
    content: bytes | None = None
    try:
        with ref.path.open("rb") as file:
            content = file.read(remaining + 1)
    except OSError:
        pass
    if content is None:
        raise InputFileNotFoundError(
            "Входной image-файл недоступен для чтения.",
            details={"parameter": "--image", "position": position},
        )
    if len(content) > remaining:
        raise InvalidParameterValueError(
            "Reference images превышают локальный safety-лимит тела запроса.",
            details={"parameter": "--image", "position": position, "max_bytes": max_body_bytes},
        )
    return content


def _status_error_code(status: int) -> tuple[str, bool | None]:
    """Сопоставить HTTP-статус с кодом нормализованной ошибки и retryable."""
    known = _STATUS_ERROR_CODES.get(status)
    if known is not None:
        return known
    if 500 <= status < 600:
        return (_PROVIDER_UNAVAILABLE, True)
    return (_PROVIDER_HTTP_ERROR, None)


def _extract_trace_id(body: bytes) -> str | None:
    """Извлечь allowlisted безопасный trace ID из error-конверта.

    Разбор ведётся отдельно от рабочего `decode_media_json`: некорректное тело не
    должно подменять HTTP-ошибку. Ошибка разбора просто не даёт trace ID. Сырое
    тело, message и URL в результат не попадают.
    """
    decoded: object = None
    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    error = decoded.get("error")
    error_trace = error.get("trace_id") if isinstance(error, dict) else None
    for candidate in (decoded.get("trace_id"), error_trace):
        if isinstance(candidate, str) and _SAFE_TRACE_ID.fullmatch(candidate) is not None:
            return candidate
    return None


__all__ = [
    "POLZA_API_BASE_URL",
    "SUBMIT_UNCERTAIN",
    "PolzaProviderGateway",
]
