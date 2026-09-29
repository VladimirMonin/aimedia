"""Диагностические события aimedia с обязательной редакцией секретов.

Контракт каналов: stdout принадлежит итоговому пользовательскому документу (в
JSON-режиме — единственному). Диагностика идёт только в stderr; логгер никогда не
пишет в stdout. Разрешённые поля и отличие diagnostics от history/evidence описаны
в `docs/plans/logging-contract.md`.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, TextIO
from urllib.parse import unquote_plus

REDACTED = "[REDACTED]"

# Части имён полей, значения которых не попадают в диагностику ни при каких
# условиях. Проверка подстроки сознательно консервативна: лучше перередактировать
# лишнее, чем допустить утечку.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)

# Дополнительные имена query-/fragment-параметров, встречающиеся в signed URL
# (AWS S3/CloudFront, Google Cloud Storage, Azure SAS, OAuth). Совпадение для
# этого набора — точное, чтобы короткие ключи вроде `sig` не сужали чужие имена.
SIGNED_URL_QUERY_KEYS: tuple[str, ...] = (
    "access_key_id",
    "awsaccesskeyid",
    "googleaccessid",
    "key_pair_id",
    "policy",
    "sas",
    "sig",
    "signature",
    "x_amz_credential",
    "x_amz_security_token",
    "x_amz_signature",
    "x_goog_credential",
    "x_goog_security_token",
    "x_goog_signature",
)

# Абсолютный URL c явной схемой. Unicode-хосты и pct-encoded userinfo попадают в
# символьный класс, а пробелы и обрамляющие кавычки/скобки-декорации — нет.
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s<>\"'`]+")
# Пара `key=value` внутри query- или `#fragment`-части.
_QUERY_PAIR_RE = re.compile(r"([^=&;#]+)=([^&;#]*)")
# Пунктуация, которая по смыслу принадлежит предложению, а не URL.
_URL_TRAILING_PUNCTUATION = ".,;!"
# Закрывающие скобки-декорации (markdown/обрамление), но не IPv6-literal `[::1]`.
_URL_CLOSING_TO_OPENING = {")": "(", "]": "[", "}": "{"}

# Разрешённые поля записи события (полный список — в logging-contract.md).
EVENT_FIELDS: tuple[str, ...] = (
    "timestamp_utc",
    "level",
    "event",
    "component",
    "command",
    "job_id",
    "provider",
    "remote_operation",
    "remote_job_id",
    "duration_ms",
    "details",
)

_CORRELATION_FIELDS: tuple[str, ...] = (
    "command",
    "job_id",
    "provider",
    "remote_operation",
    "remote_job_id",
)

LEVEL_ORDER: Mapping[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

_CORRELATION: ContextVar[Mapping[str, Any]] = ContextVar("aimedia_log_correlation")
_CORRELATION.set({})


def _correlation_snapshot() -> dict[str, Any]:
    current = _CORRELATION.get(None)
    return dict(current) if current is not None else {}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _is_sensitive_query_key(key: str) -> bool:
    """Чувствителен ли query-/fragment-ключ URL.

    Имя ключа проверяется так, как его разберёт query-parser: один слой
    percent-decoding, а `+` трактуется как пробел-разделитель
    (`unquote_plus` + пробел→`_`). Без этого `%73ig` или
    `X%2DAmz%2DSignature` прошли бы мимо проверки. Затем переиспользуется
    консервативная проверка имён полей (`token`, `secret`, …) и точный список
    signed URL variants.

    Декодируется только имя; исходное написание URL и значения безопасных
    параметров в выводе сохраняются.
    """
    decoded = unquote_plus(key)
    normalized = decoded.replace(" ", "_")
    if _is_sensitive_key(normalized):
        return True
    return normalized.lower().replace("-", "_") in SIGNED_URL_QUERY_KEYS


def _redact_query_pairs(part: str) -> str:
    """Затемнить значения чувствительных пар в query или `#fragment`-части.

    `part` начинается с разделителя (`?`/`#`) и сохраняется как есть, чтобы не
    менять структуру URL. Незнакомое значение удаляется целиком, а ключ и порядок
    остальных пар остаются.
    """
    if not part:
        return part

    def _replace(match: re.Match[str]) -> str:
        key, value = match.group(1), match.group(2)
        if value and _is_sensitive_query_key(key):
            return f"{key}={REDACTED}"
        return match.group(0)

    return part[0] + _QUERY_PAIR_RE.sub(_replace, part[1:])


def _sanitize_url(url: str) -> str:
    """Убрать userinfo и подписи из абсолютного URL, сохранив хост/путь/параметры.

    Обрабатываются только URL с явной схемой (`scheme://`). Границы контракта
    описаны в `docs/plans/logging-contract.md`: это не универсальный DLP, а
    точечная редакция известных носителей credentials.
    """
    scheme_end = url.find("://")
    if scheme_end == -1:
        return url
    scheme = url[: scheme_end + 3]
    rest = url[scheme_end + 3 :]

    fragment = ""
    hash_pos = rest.find("#")
    if hash_pos != -1:
        fragment = rest[hash_pos:]
        rest = rest[:hash_pos]

    query = ""
    query_pos = rest.find("?")
    if query_pos != -1:
        query = rest[query_pos:]
        rest = rest[:query_pos]

    slash_pos = rest.find("/")
    if slash_pos == -1:
        authority, path = rest, ""
    else:
        authority, path = rest[:slash_pos], rest[slash_pos:]

    userinfo_sep = authority.rfind("@")
    if userinfo_sep != -1:
        authority = REDACTED + authority[userinfo_sep:]

    return f"{scheme}{authority}{path}{_redact_query_pairs(query)}{_redact_query_pairs(fragment)}"


def _redact_url_match(match: re.Match[str]) -> str:
    """Отредактировать одно URL-совпадение, вернув хвостовую декорацию на место."""
    url = match.group(0)
    core = url.rstrip(_URL_TRAILING_PUNCTUATION)
    tail = url[len(core) :]
    while core and core[-1] in _URL_CLOSING_TO_OPENING:
        closing = core[-1]
        opening = _URL_CLOSING_TO_OPENING[closing]
        # Парная закрывающая скобка — часть URL (например `[::1]`), одиночная —
        # декорация обрамления и возвращается в текст без редакции.
        if core.count(closing) <= core.count(opening):
            break
        tail = closing + tail
        core = core[:-1]
    return _sanitize_url(core) + tail


def redact_text(text: str, secrets: Sequence[str] = ()) -> str:
    """Убрать известные секреты и URL-credentials из текста.

    Помимо подстановки известных `secrets` выполняется структурная редакция URL:
    userinfo и значения чувствительных query-/fragment-параметров заменяются без
    знания их значения. Хост, путь и безопасные параметры сохраняются.
    """
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    return _URL_RE.sub(_redact_url_match, result)


def redact(value: Any, secrets: Sequence[str] = ()) -> Any:
    """Рекурсивно отредактировать значение, включая вложенные details."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, BaseException):
        return format_exception(value, secrets)
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value


def format_exception(exc: BaseException, secrets: Sequence[str] = ()) -> str:
    """Отформатировать исключение, удалив из трассировки известные секреты."""
    return redact_text("".join(traceback.format_exception(exc)), secrets)


def format_exc_info(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None],
    secrets: Sequence[str] = (),
) -> str:
    """Отформатировать `sys.exc_info()`-совместимый кортеж с редакцией секретов.

    `traceback.format_exception` разворачивает всю цепочку `__cause__`/`__context__`
    вместе с аргументами исключений, поэтому одного прохода редакции достаточно и
    для вложенных исключений. Пустой кортеж `(None, None, None)` даёт пустую строку.
    """
    return redact_text("".join(traceback.format_exception(*exc_info)), secrets)


def _redact_log_args(args: Any, secrets: Sequence[str]) -> Any:
    """Отредактировать `record.args`, сохранив тип контейнера.

    `redact()` нормализует `tuple` в `list` ради JSON, но `record.args` участвует в
    подстановке `msg % args`: смена кортежа на список ломает форматирование и может
    раскрыть аргументы сырыми. Поэтому кортеж и mapping сохраняют свой тип.
    """
    if isinstance(args, tuple):
        return tuple(_redact_log_args(item, secrets) for item in args)
    if isinstance(args, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else _redact_log_args(value, secrets)
            for key, value in args.items()
        }
    return redact(args, secrets)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class EventContext:
    """Корреляция события: команда и идентификаторы будущего Job."""

    command: str | None = None
    job_id: str | None = None
    provider: str | None = None
    remote_operation: str | None = None
    remote_job_id: str | None = None


@dataclass(frozen=True)
class EventLogger:
    """Логгер структурированных событий с редакцией секретов.

    Значения секретов используются только как цели редакции и никогда не попадают
    в запись: `config_loaded` получает исключительно имена источников.
    """

    component: str = "cli"
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    secrets: tuple[str, ...] = ()
    min_level: str = "INFO"
    context: EventContext = field(default_factory=EventContext)

    def child(self, **context: Any) -> EventLogger:
        """Вернуть логгер с расширенной корреляцией, не меняя исходный."""
        merged = {name: getattr(self.context, name) for name in _CORRELATION_FIELDS}
        merged.update({k: v for k, v in context.items() if v is not None})
        return replace(self, context=EventContext(**merged))

    def allows(self, level: str) -> bool:
        """Проверить, проходит ли уровень порог логирования."""
        return LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.min_level, 0)

    def event(
        self,
        event: str,
        *,
        level: str = "INFO",
        details: Mapping[str, Any] | None = None,
        exc: BaseException | None = None,
        duration_ms: int | None = None,
        omit_correlation_fields: Sequence[str] = (),
        **overrides: Any,
    ) -> dict[str, Any]:
        """Собрать и (при достаточном уровне) записать событие, вернув запись.

        `omit_correlation_fields` удаляет унаследованные поля для событий, где
        значение нельзя безопасно брать из context или correlation.
        """
        effective: dict[str, Any] = {
            name: getattr(self.context, name) for name in _CORRELATION_FIELDS
        }
        effective.update(_correlation_snapshot())
        effective.update({k: v for k, v in overrides.items() if v is not None})
        for name in omit_correlation_fields:
            effective.pop(name, None)

        safe_details = redact(dict(details or {}), self.secrets)
        if exc is not None:
            safe_details["exception"] = format_exception(exc, self.secrets)

        record: dict[str, Any] = {
            "timestamp_utc": _utc_now(),
            "level": level,
            "event": event,
            "component": self.component,
        }
        for name in _CORRELATION_FIELDS:
            value = effective.get(name)
            if value is not None:
                record[name] = redact_text(str(value), self.secrets)
        if duration_ms is not None:
            record["duration_ms"] = int(duration_ms)
        if safe_details:
            record["details"] = safe_details

        if self.allows(level):
            self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.stream.flush()
        return record

    def app_started(self, *, command: str, version: str) -> dict[str, Any]:
        """Событие запуска команды. Не содержит конфигурационных значений."""
        return self.event(
            "app_started",
            command=command,
            details={"version": version},
        )

    def config_loaded(self, sources: Sequence[str]) -> dict[str, Any]:
        """Событие загрузки конфигурации: только имена источников, без значений."""
        return self.event("config_loaded", details={"sources": list(sources)})

    def command_finished(self, *, exit_code: int, duration_ms: int) -> dict[str, Any]:
        """Событие окончания команды с её итоговым exit code."""
        return self.event(
            "command_finished",
            duration_ms=duration_ms,
            details={"exit_code": int(exit_code)},
        )


@contextmanager
def correlation(**fields: Any) -> Iterator[None]:
    """Изолировать корреляцию событий в текущем контексте выполнения.

    `contextvars` не даёт контексту одного batch-Job перетечь в соседний
    coroutine или поток.
    """
    current = _correlation_snapshot()
    current.update({k: v for k, v in fields.items() if v is not None})
    token = _CORRELATION.set(current)
    try:
        yield
    finally:
        _CORRELATION.reset(token)


class RedactingFilter(logging.Filter):
    """Фильтр stdlib-логирования, вырезающий секреты из сообщений."""

    def __init__(self, secrets: Sequence[str] = ()) -> None:
        super().__init__()
        self.secrets: tuple[str, ...] = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        """Отредактировать все поверхности записи до её форматирования.

        `Formatter` строит трассировку из `record.exc_info`/`record.stack_info` уже
        после фильтра, поэтому сырой `exc_info` обязан быть заменён заранее
        отредактированным текстом. Иначе секрет из `except`-ветки попадает в stderr
        в обход редакции.
        """
        record.msg = redact_text(str(record.msg), self.secrets)
        record.args = _redact_log_args(record.args, self.secrets)
        if record.exc_info:
            record.exc_text = format_exc_info(record.exc_info, self.secrets)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text, self.secrets)
        if record.stack_info:
            record.stack_info = redact_text(str(record.stack_info), self.secrets)
        return True


def configure_diagnostics(
    *,
    level: str = "INFO",
    secrets: Sequence[str] = (),
    stream: TextIO | None = None,
) -> EventLogger:
    """Настроить диагностический канал и вернуть логгер событий."""
    target = stream if stream is not None else sys.stderr
    event_logger = EventLogger(
        stream=target,
        secrets=tuple(s for s in secrets if s),
        min_level=level,
    )

    stdlib_logger = logging.getLogger("aimedia")
    stdlib_logger.handlers.clear()
    handler = logging.StreamHandler(target)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter(event_logger.secrets))
    stdlib_logger.addHandler(handler)
    stdlib_logger.setLevel(LEVEL_ORDER.get(level, LEVEL_ORDER["INFO"]))
    stdlib_logger.propagate = False
    return event_logger


@dataclass
class _RunState:
    logger: EventLogger
    started_monotonic: float


_ACTIVE: ContextVar[_RunState | None] = ContextVar("aimedia_active_logger", default=None)


def activate(logger: EventLogger) -> None:
    """Сделать логгер активным и засечь начало команды."""
    _ACTIVE.set(_RunState(logger=logger, started_monotonic=time.monotonic()))


def active_logger() -> EventLogger | None:
    """Вернуть активный логгер текущего контекста, если он есть."""
    state = _ACTIVE.get()
    return None if state is None else state.logger


def finish_active(exit_code: int) -> dict[str, Any] | None:
    """Записать `command_finished` и снять активацию текущего контекста."""
    state = _ACTIVE.get()
    if state is None:
        return None
    _ACTIVE.set(None)
    duration_ms = int((time.monotonic() - state.started_monotonic) * 1000)
    return state.logger.command_finished(exit_code=exit_code, duration_ms=duration_ms)
