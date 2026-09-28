"""Canary-проверки редакции секретов в диагностике.

Один тестовый секрет помещается в сообщение события, во вложенные `details` и в
форматируемое исключение. Проверяется, что он не появляется ни в одном канале:
ни в записи события, ни в stdlib-логе, ни в stdout.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from aimedia.logging import (
    REDACTED,
    EventLogger,
    RedactingFilter,
    configure_diagnostics,
    correlation,
    format_exc_info,
    format_exception,
    redact,
    redact_text,
)

# Намеренный тестовый «секрет». Значение синтетическое и не является реальным
# ключом; оно служит только целью редакции.
CANARY_SECRET = "sk-canary-0123456789abcdef"


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_redact_text_removes_known_secret() -> None:
    assert CANARY_SECRET not in redact_text(f"prefix {CANARY_SECRET} suffix", (CANARY_SECRET,))
    assert REDACTED in redact_text(f"prefix {CANARY_SECRET}", (CANARY_SECRET,))


def test_redact_removes_sensitive_keys_recursively() -> None:
    value = {"nested": {"deep": [{"api_key": "x", "token": "y", "note": "keep"}]}}
    result = redact(value)
    assert result["nested"]["deep"][0]["api_key"] == REDACTED
    assert result["nested"]["deep"][0]["token"] == REDACTED
    assert result["nested"]["deep"][0]["note"] == "keep"


def test_format_exception_redacts_canary() -> None:
    try:
        raise RuntimeError(f"boom {CANARY_SECRET}")
    except RuntimeError as exc:
        formatted = format_exception(exc, (CANARY_SECRET,))
    assert CANARY_SECRET not in formatted
    assert REDACTED in formatted


def test_canary_absent_from_message_details_and_exception() -> None:
    """Сквозная проверка всех внутренних поверхностей одной записи события."""
    stream = io.StringIO()
    logger = EventLogger(stream=stream, secrets=(CANARY_SECRET,))
    record = logger.event(
        "canary",
        details={
            "message": f"provider said {CANARY_SECRET}",
            "nested": {"list": [{"secret": CANARY_SECRET}]},
            "authorization": f"Bearer {CANARY_SECRET}",
        },
    )
    try:
        raise ValueError(f"inner {CANARY_SECRET}")
    except ValueError as exc:
        logger.event("canary_error", details={"outer": {"inner": CANARY_SECRET}}, exc=exc)

    written = stream.getvalue()
    assert CANARY_SECRET not in written
    assert CANARY_SECRET not in json.dumps(record, ensure_ascii=False)
    assert REDACTED in written


def test_canary_absent_from_stdlib_logging() -> None:
    stream = io.StringIO()
    configure_diagnostics(level="DEBUG", secrets=(CANARY_SECRET,), stream=stream)
    logging.getLogger("aimedia.test").error("token=%s", CANARY_SECRET)
    assert CANARY_SECRET not in stream.getvalue()
    assert REDACTED in stream.getvalue()


def test_format_exc_info_redacts_nested_chain() -> None:
    """Форматирование `sys.exc_info()` удаляет секрет из вложенной цепочки причин."""
    try:
        try:
            raise RuntimeError(f"inner {CANARY_SECRET}")
        except RuntimeError as inner:
            raise ValueError(f"outer {CANARY_SECRET}") from inner
    except ValueError:
        import sys

        formatted = format_exc_info(sys.exc_info(), (CANARY_SECRET,))
    assert CANARY_SECRET not in formatted
    # Оба звена цепочки (`inner` и `outer`) прошли редакцию.
    assert formatted.count(REDACTED) == 2


def test_canary_absent_from_stdlib_exc_info() -> None:
    """`Logger.exception`/`exc_info=True` не обходит фильтр редакции.

    Регрессия: `Formatter` строит трассировку после фильтра, поэтому сырой
    `record.exc_info` утёк бы в stderr. Проверяются оба звена вложенной цепочки и
    аргументы сообщения.
    """
    stream = io.StringIO()
    configure_diagnostics(level="DEBUG", secrets=(CANARY_SECRET,), stream=stream)
    logger = logging.getLogger("aimedia.test.exc")
    try:
        try:
            raise RuntimeError(f"inner {CANARY_SECRET}")
        except RuntimeError as inner:
            raise ValueError(f"outer {CANARY_SECRET}") from inner
    except ValueError:
        logger.exception("command failed: token=%s", CANARY_SECRET)

    written = stream.getvalue()
    assert CANARY_SECRET not in written
    assert written.count(REDACTED) == 3
    assert "Traceback (most recent call last)" in written


def test_canary_absent_from_stdlib_exc_info_explicit() -> None:
    """Явный `exc_info=<tuple>` также редактируется до форматирования."""
    stream = io.StringIO()
    configure_diagnostics(level="DEBUG", secrets=(CANARY_SECRET,), stream=stream)
    logger = logging.getLogger("aimedia.test.exc_tuple")
    try:
        raise KeyError(f"missing {CANARY_SECRET}")
    except KeyError:
        import sys

        logger.error("lookup failed", exc_info=sys.exc_info())
    assert CANARY_SECRET not in stream.getvalue()
    assert REDACTED in stream.getvalue()


def test_stdlib_stack_info_is_redacted() -> None:
    """Текст `stack_info` тоже не остаётся сырым в записи."""
    record = logging.LogRecord(
        name="aimedia.test.stack",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="context",
        args=(),
        exc_info=None,
    )
    record.stack_info = f"frame {CANARY_SECRET}"
    assert RedactingFilter((CANARY_SECRET,)).filter(record) is True
    assert CANARY_SECRET not in str(record.stack_info)
    assert REDACTED in str(record.stack_info)


def test_logger_never_writes_to_stdout(capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    """Диагностика уходит в переданный поток, а не в stdout."""
    stream = io.StringIO()
    logger = EventLogger(stream=stream)
    logger.event("channel_check", details={"ok": True})
    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert "channel_check" in stream.getvalue()


def test_min_level_suppresses_lower_priority_events() -> None:
    stream = io.StringIO()
    logger = EventLogger(stream=stream, min_level="WARNING")
    logger.event("debug_event", level="DEBUG")
    logger.event("warn_event", level="WARNING")
    events = _events(stream)
    assert [event["event"] for event in events] == ["warn_event"]


def test_event_returns_record_even_when_level_suppressed() -> None:
    logger = EventLogger(stream=io.StringIO(), min_level="ERROR")
    record = logger.event("suppressed")
    assert record["event"] == "suppressed"


def test_config_loaded_reports_only_source_names() -> None:
    """`config_loaded` не содержит значений настроек, путей или секрета."""
    stream = io.StringIO()
    logger = EventLogger(stream=stream, secrets=(CANARY_SECRET,))
    logger.config_loaded(("cli", "config", "env", "default"))
    event = _events(stream)[0]
    assert event["details"] == {"sources": ["cli", "config", "env", "default"]}
    assert CANARY_SECRET not in stream.getvalue()


def test_correlation_is_isolated_between_contexts() -> None:
    stream = io.StringIO()
    logger = EventLogger(stream=stream)
    with correlation(job_id="job-a"):
        logger.event("first")
    logger.event("second")
    first, second = _events(stream)
    assert first["job_id"] == "job-a"
    assert "job_id" not in second


def test_child_logger_extends_correlation_without_mutation() -> None:
    parent = EventLogger(stream=io.StringIO())
    child = parent.child(job_id="job-1")
    assert parent.context.job_id is None
    assert child.context.job_id == "job-1"
