"""Чистая нормализация ответов Polza Media (`POST /v1/media`, `GET /v1/media/{id}`).

Модуль только преобразует уже разобранный JSON в доменные значения: ни HTTP-клиента,
ни auth, ни загрузки файлов (они относятся к C09c). Публичная точка входа —
:func:`normalize_media_submission`, :func:`normalize_media_status` и
:func:`normalize_media_result`; :func:`decode_media_json` — единственный санкционированный
декодер тела, гарантирующий точные `Decimal` для чисел.

Схема-источник: `docs/Get Media.txt` (ответ `MediaStatusPresenter`, `usage`,
`error`) и `docs/Post Media.txt` (ответ `pending` того же презентера). Примеры и
model ID из документации — свидетельство схемы, а не подтверждённая живая
поддержка.

Инварианты:

- `pending` → `SUBMITTED`, `processing` → `RUNNING`, `completed` с хотя бы одним
  пригодным image artifact → `COMPLETED`, `cancelled` → типизированный
  не-success (`CANCELLED`), `failed` поднимает `ProviderError` с `JobError`;
- `failed` не протаскивает сырой текст provider: `provider_code` и
  `metadata.provider_name` допускаются только как ограниченные machine IDs;
  `provider_message` не заполняется;
- `completed` без `data.url` (text-only, отсутствующий `data`, нераспознанная форма)
  — безопасная ошибка `PROVIDER_INVALID_RESPONSE`, а не выдуманный success;
- обязательны безопасный односегментный `id`, `object == "media.generation"` и
  документированный `status`; неизвестный тип/форма закрываются ошибкой (fail closed);
  транспорт C09c повторно проверяет/кодирует ID при построении GET URL;
- `RemoteJobRef` получает `provider_id="polza"`, `operation=MEDIA` и сохранённый
  `remote_job_id`;
- `usage.raw` сохраняет исходный usage целиком; `cost` строится один раз, поэтому
  aliases `cost_rub`/`cost` не удваиваются: `cost_rub` предпочтителен даже при нуле,
  оба отсутствуют → `None` (неизвестная цена), валюта всегда `RUB`;
- деньги принимаются только как `Decimal`, `int` или строка; `float`, `bool`,
  nonfinite и отрицательные значения отклоняются. Транспорт C09c обязан декодировать
  тело через :func:`decode_media_json` (`parse_float=Decimal`, запрет JSON
  `NaN`/`Infinity`), чтобы дробные числа не проходили через двоичную дробь;
  чтобы дробные числа не проходили через двоичную дробь;
- счётчики usage отсутствующее поле оставляет `None`, а не `0`; ноль — известное
  значение. Неизвестные ключи usage не теряются, но и не интерпретируются.

Сообщения об ошибках и `details` не содержат prompt, query URL, base64, имени
модели или сырого текста provider; исключения не связываются с исходной причиной
(`__cause__`/`__context__` пусты).
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Final, TypeGuard
from urllib.parse import urlsplit

from aimedia.domain.artifacts import ArtifactKind, RemoteArtifact
from aimedia.domain.costs import Cost, Usage
from aimedia.domain.errors import JobError, ProviderError
from aimedia.domain.ports import ProviderResult, SubmissionResult
from aimedia.domain.refs import ProviderJobState, RemoteJobRef, RemoteOperation

POLZA_PROVIDER_ID: Final = "polza"
"""`ProviderRef.id` adapter'а Polza; совпадает с `RemoteJobRef.provider_id`."""

_MEDIA_OBJECT: Final = "media.generation"
_PROVIDER_INVALID_RESPONSE: Final = "PROVIDER_INVALID_RESPONSE"
_REMOTE_GENERATION_FAILED: Final = "REMOTE_GENERATION_FAILED"
_RUB_CURRENCY: Final = "RUB"
_COST_ALIASES: Final[tuple[str, ...]] = ("cost_rub", "cost")
# Локальные ограничения безопасной сериализации, НЕ лимиты Polza или модели.
_MAX_COST_FIXED_CHARS: Final = 4096  # domain/storage serialize Decimal via format(amount, "f")
_MAX_SAFE_TOKENS: Final = 2**63 - 1  # SQLite signed INTEGER
_MIN_SAFE_TIMESTAMP: Final = -62135596800  # 0001-01-01 00:00:00 UTC
_MAX_SAFE_TIMESTAMP: Final = 253402300799  # 9999-12-31 23:59:59 UTC
_REMOTE_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MACHINE_ID_PATTERN: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?\Z"
)
_STATUS_MAP: Final[dict[str, ProviderJobState]] = {
    "pending": ProviderJobState.SUBMITTED,
    "processing": ProviderJobState.RUNNING,
    "completed": ProviderJobState.COMPLETED,
    "failed": ProviderJobState.FAILED,
    "cancelled": ProviderJobState.CANCELLED,
}

_MISSING: Final = object()


def decode_media_json(payload: str | bytes) -> object:
    """Декодировать тело ответа, сохраняя дробные числа как точные `Decimal`.

    Единственный санкционированный способ превратить тело Polza Media в объект:
    транспорт C09c не должен использовать `json.loads` без `parse_float=Decimal`
    и запрета нестандартных `NaN`/`Infinity`: иначе стоимость может стать неточной,
    а произвольный `usage.raw` — содержать несерилизуемые числа. Некорректный JSON
    становится безопасной `PROVIDER_INVALID_RESPONSE` без исходной причины.
    """
    decoded: object = _MISSING
    try:
        decoded = json.loads(
            payload, parse_float=Decimal, parse_constant=_reject_nonfinite_constant
        )
    except (ValueError, UnicodeError):
        decoded = _MISSING
    if decoded is _MISSING:
        raise _invalid_response("malformed_json")
    return decoded


def _reject_nonfinite_constant(_value: str) -> None:
    """Запретить не-JSON числовые константы без включения их текста в ошибку."""
    raise ValueError("nonfinite JSON number")


def normalize_media_submission(payload: object) -> SubmissionResult:
    """Нормализовать ответ `POST /v1/media` (и синхронный completed) в `SubmissionResult`.

    `pending`/`processing` дают `SUBMITTED`/`RUNNING` вместе с `remote_ref`;
    `completed` даёт `COMPLETED` с разобранным `ProviderResult`; `cancelled` даёт
    типизированный `CANCELLED`; `failed` поднимает `ProviderError`. Некорректный
    обязательный конверт (id/object/status) или `completed` без пригодного image
    artifact — `PROVIDER_INVALID_RESPONSE`.
    """
    mapping = _require_mapping(payload)
    remote_ref = _remote_ref(_read_remote_job_id(mapping))
    _read_object(mapping)
    state = _read_status(mapping)
    if state is ProviderJobState.FAILED:
        raise _provider_failure(mapping)
    if state is ProviderJobState.COMPLETED:
        return SubmissionResult(
            state=state,
            remote_ref=remote_ref,
            result=_parse_completed_result(mapping),
        )
    return SubmissionResult(state=state, remote_ref=remote_ref)


def validate_media_response_id(payload: object, expected_id: str) -> None:
    """Проверить безопасный ID GET-ответа до нормализации его состояния/результата."""
    actual_id = _read_remote_job_id(_require_mapping(payload))
    if actual_id != expected_id:
        raise _invalid_response("remote_job_id_mismatch")


def normalize_media_status(payload: object) -> ProviderJobState:
    """Нормализовать ответ `GET /v1/media/{id}` в `ProviderJobState`.

    Сырые статусы приводятся к доменной модели; `failed` поднимает `ProviderError`
    с нормализованным отказом, а не возвращается особым значением.
    """
    mapping = _require_mapping(payload)
    _read_remote_job_id(mapping)
    _read_object(mapping)
    state = _read_status(mapping)
    if state is ProviderJobState.FAILED:
        raise _provider_failure(mapping)
    return state


def normalize_media_result(payload: object) -> ProviderResult:
    """Нормализовать completed-ответ `GET /v1/media/{id}` в `ProviderResult`.

    Требует `status == completed`; любой другой терминальный/промежуточный статус
    или отсутствие пригодного image artifact — `PROVIDER_INVALID_RESPONSE`.
    """
    mapping = _require_mapping(payload)
    _read_remote_job_id(mapping)
    _read_object(mapping)
    state = _read_status(mapping)
    if state is ProviderJobState.FAILED:
        raise _provider_failure(mapping)
    if state is not ProviderJobState.COMPLETED:
        raise _invalid_response("status_not_completed")
    return _parse_completed_result(mapping)


def _parse_completed_result(mapping: dict[str, object]) -> ProviderResult:
    """Собрать `ProviderResult` из completed-конверта, не выдумывая success."""
    artifacts = _parse_remote_artifacts(mapping)
    if not artifacts:
        raise _invalid_response("no_usable_image")
    return ProviderResult(
        remote_artifacts=artifacts,
        content=_read_content(mapping),
        usage=_parse_usage(mapping),
        cost=_parse_cost(mapping),
        provider_metadata=_read_provider_metadata(mapping),
    )


def _remote_ref(remote_job_id: str) -> RemoteJobRef:
    """Ссылка на удалённое media-задание Polza с сохранённым ID."""
    return RemoteJobRef(
        provider_id=POLZA_PROVIDER_ID,
        remote_job_id=remote_job_id,
        operation=RemoteOperation.MEDIA,
    )


def _require_mapping(payload: object) -> dict[str, object]:
    """Потребовать JSON-объект верхнего уровня."""
    if isinstance(payload, dict):
        return payload
    raise _invalid_response("response_not_object")


def _read_remote_job_id(mapping: dict[str, object]) -> str:
    """Прочитать ограниченный односегментный `id` без переформатирования."""
    value = mapping.get("id", _MISSING)
    if isinstance(value, str) and _REMOTE_ID_PATTERN.fullmatch(value):
        return value
    raise _invalid_response("missing_id")


def _read_object(mapping: dict[str, object]) -> None:
    """Потребовать документированный `object == "media.generation"`."""
    if mapping.get("object", _MISSING) != _MEDIA_OBJECT:
        raise _invalid_response("invalid_object")


def _read_status(mapping: dict[str, object]) -> ProviderJobState:
    """Привести документированный `status` к доменному состоянию."""
    raw = mapping.get("status", _MISSING)
    if not isinstance(raw, str) or raw not in _STATUS_MAP:
        raise _invalid_response("invalid_status")
    return _STATUS_MAP[raw]


def _parse_remote_artifacts(mapping: dict[str, object]) -> list[RemoteArtifact]:
    """Извлечь пригодные image artifacts из `data`.

    Документирована форма `data.url`. Дополнительно распознаётся синтетическая
    (не подтверждённая документацией) форма `data` как списка объектов с `url` —
    она нужна для нескольких артефактов и помечается в тестах как synthetic. Любая
    другая форма закрывается ошибкой: неизвестный shape не превращается в success.
    """
    data = mapping.get("data", _MISSING)
    if data is _MISSING or data is None:
        return []
    if isinstance(data, dict):
        url = data.get("url", _MISSING)
        if _valid_image_url(url):
            return [RemoteArtifact(kind=ArtifactKind.IMAGE, url=url)]
        raise _invalid_response("invalid_data")
    if isinstance(data, list):
        artifacts: list[RemoteArtifact] = []
        for item in data:
            if not isinstance(item, dict):
                raise _invalid_response("invalid_data")
            url = item.get("url", _MISSING)
            if not _valid_image_url(url):
                raise _invalid_response("invalid_data")
            artifacts.append(RemoteArtifact(kind=ArtifactKind.IMAGE, url=url))
        return artifacts
    raise _invalid_response("invalid_data")


def _valid_image_url(value: object) -> TypeGuard[str]:
    """Accept only absolute HTTPS image locations; C09c still guards CDN/SSRF."""
    if not isinstance(value, str) or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port != 0
        )
    except ValueError:
        return False


def _safe_machine_id(value: object) -> bool:
    """Constrain diagnostic identifiers to short ASCII machine tokens, never prose/URLs."""
    return (
        isinstance(value, str)
        and len(value) <= 128
        and _MACHINE_ID_PATTERN.fullmatch(value) is not None
    )


def _read_content(mapping: dict[str, object]) -> str | None:
    """Прочитать текстовый ответ модели, если он есть и корректен."""
    value = mapping.get("content", _MISSING)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, str):
        return value
    raise _invalid_response("invalid_content")


def _parse_usage(mapping: dict[str, object]) -> Usage | None:
    """Нормализовать `usage`, сохранив исходный объект целиком в `raw`."""
    raw_usage = mapping.get("usage", _MISSING)
    if raw_usage is _MISSING or raw_usage is None:
        return None
    if not isinstance(raw_usage, dict):
        raise _invalid_response("invalid_usage")
    return Usage(
        input_tokens=_optional_token(raw_usage, "input_tokens"),
        output_tokens=_optional_token(raw_usage, "output_tokens"),
        total_tokens=_optional_token(raw_usage, "total_tokens"),
        input_units=_optional_units(raw_usage, "input_units"),
        output_units=_optional_units(raw_usage, "output_units"),
        duration_seconds=_optional_units(raw_usage, "duration_seconds"),
        raw=dict(raw_usage),
    )


def _parse_cost(mapping: dict[str, object]) -> Cost | None:
    """Построить одну `Cost` в RUB из `cost_rub` (приоритет) либо alias `cost`.

    `cost_rub` предпочтителен даже при нулевом значении; отсутствие обоих — `None`
    (неизвестная цена, а не ноль). Malformed присутствующее значение — ошибка, без
    молчаливого перехода к alias.
    """
    raw_usage = mapping.get("usage", _MISSING)
    if raw_usage is _MISSING or raw_usage is None:
        return None
    if not isinstance(raw_usage, dict):
        raise _invalid_response("invalid_usage")
    for key in _COST_ALIASES:
        value = raw_usage.get(key, _MISSING)
        if value is _MISSING or value is None:
            continue
        amount = _nonneg_decimal(value)
        if amount is None or _cost_fixed_chars(amount) > _MAX_COST_FIXED_CHARS:
            raise _invalid_response("invalid_cost")
        return Cost(amount=amount, currency=_RUB_CURRENCY)
    return None


def _cost_fixed_chars(amount: Decimal) -> int:
    """Длина `format(amount, "f")` без развёртывания Decimal в строку."""
    sign, digits, exponent = amount.as_tuple()
    if not isinstance(exponent, int):
        return _MAX_COST_FIXED_CHARS + 1  # nonfinite sentinel; fail closed
    places = len(digits)
    if exponent >= 0:
        return sign + places + exponent
    if places + exponent > 0:
        return sign + places + 1  # точка внутри коэффициента
    return sign + 2 - exponent  # `0.` и все дробные разряды (включая нули)


def _read_provider_metadata(mapping: dict[str, object]) -> dict[str, object]:
    """Собрать allowlisted provider metadata без prompt, URL query и сырых ошибок."""
    metadata: dict[str, object] = {}
    model = mapping.get("model", _MISSING)
    if model is not _MISSING and model is not None:
        if _safe_machine_id(model):
            metadata["model"] = model
    for key in ("created", "completed_at"):
        timestamp = _optional_timestamp(mapping, key)
        if timestamp is not None:
            metadata[key] = timestamp
    warnings = mapping.get("warnings", _MISSING)
    if warnings is not _MISSING and warnings is not None:
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise _invalid_response("invalid_warnings")
        metadata["warning_count"] = len(warnings)
    return metadata


def _optional_token(mapping: dict[str, object], key: str) -> int | None:
    """Прочитать счётчик в пределах SQLite signed INTEGER, не лимита модели."""
    value = mapping.get(key, _MISSING)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= _MAX_SAFE_TOKENS:
            return value
        raise _invalid_response("invalid_usage")
    number = _nonneg_decimal(value)
    if number is None or number > _MAX_SAFE_TOKENS or number != number.to_integral_value():
        raise _invalid_response("invalid_usage")
    token: int | None = None
    try:
        token = int(number)
    except (OverflowError, TypeError, ValueError):
        pass
    if token is None:
        raise _invalid_response("invalid_usage")
    return token


def _optional_units(mapping: dict[str, object], key: str) -> float | None:
    """Прочитать неотрицательное число единиц/длительности usage как `float`."""
    value = mapping.get(key, _MISSING)
    if value is _MISSING or value is None:
        return None
    number = _nonneg_decimal(value)
    if number is None:
        raise _invalid_response("invalid_usage")
    units: float | None = None
    try:
        units = float(number)
    except (OverflowError, TypeError, ValueError):
        pass
    if units is None or not math.isfinite(units):
        raise _invalid_response("invalid_usage")
    return units


def _optional_timestamp(mapping: dict[str, object], key: str) -> int | None:
    """Прочитать Unix timestamp в пределах календаря UTC 0001–9999 (локальная защита)."""
    value = mapping.get(key, _MISSING)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool):
        raise _invalid_response("invalid_timestamp")
    if isinstance(value, int):
        if _MIN_SAFE_TIMESTAMP <= value <= _MAX_SAFE_TIMESTAMP:
            return value
        raise _invalid_response("invalid_timestamp")
    if (
        isinstance(value, Decimal)
        and value.is_finite()
        and _MIN_SAFE_TIMESTAMP <= value <= _MAX_SAFE_TIMESTAMP
        and value == value.to_integral_value()
    ):
        timestamp: int | None = None
        try:
            timestamp = int(value)
        except (OverflowError, TypeError, ValueError):
            pass
        if timestamp is not None:
            return timestamp
    raise _invalid_response("invalid_timestamp")


def _nonneg_decimal(value: object) -> Decimal | None:
    """Привести число к конечному неотрицательному `Decimal` или вернуть `None`.

    `float` и `bool` отклоняются: деньги и счётчики не должны проходить через
    двоичную дробь или булев флаг. Строка разбирается точно; NaN/Infinity и
    отрицательные значения недопустимы.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        try:
            decimal_value = Decimal(value)
        except (InvalidOperation, OverflowError, TypeError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            decimal_value = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    if not decimal_value.is_finite() or decimal_value < 0:
        return None
    return decimal_value


def _provider_failure(mapping: dict[str, object]) -> ProviderError:
    """Нормализовать `failed` без сырого текста provider и traceback.

    Сохраняются только нормализованный code и безопасные machine IDs в
    `provider_code`/`metadata.provider_name`. Сырой текст provider не переносится.
    """
    provider_code: str | None = None
    details: dict[str, object] = {}
    error = mapping.get("error", _MISSING)
    if isinstance(error, dict):
        code = error.get("code", _MISSING)
        if _safe_machine_id(code):
            provider_code = code
        raw_metadata = error.get("metadata", _MISSING)
        if isinstance(raw_metadata, dict):
            provider_name = raw_metadata.get("provider_name", _MISSING)
            if _safe_machine_id(provider_name):
                details["provider_name"] = provider_name
    return ProviderError(
        JobError(
            code=_REMOTE_GENERATION_FAILED,
            message="Провайдер сообщил об ошибке генерации.",
            provider_code=provider_code,
            details=details,
        )
    )


def _invalid_response(reason: str) -> ProviderError:
    """Безопасный типизированный отказ на некорректный ответ provider.

    `reason` — машинный код из фиксированного набора вызовов; пользовательские
    данные, prompt и сырой текст provider в `details` не попадают.
    """
    return ProviderError(
        JobError(
            code=_PROVIDER_INVALID_RESPONSE,
            message="Провайдер вернул некорректный или неполный ответ.",
            details={"reason": reason},
        )
    )


__all__ = [
    "POLZA_PROVIDER_ID",
    "decode_media_json",
    "normalize_media_result",
    "normalize_media_status",
    "normalize_media_submission",
    "validate_media_response_id",
]
