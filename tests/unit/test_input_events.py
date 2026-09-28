"""События application-границы C04: `prompt_compiled`, `input_prepared`, `validation_failed`.

Логирование проверяется на существующем `EventLogger` (`aimedia.logging`), а не на
новом механизме. Главное утверждение: в записях есть только числа, размеры и код
ошибки — ни текста prompt, ни содержимого файлов, ни путей источников.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from image_fixtures import jpeg_bytes, png_bytes

from aimedia.application.inputs import ReferenceLimits, prepare_reference_images
from aimedia.application.prompts import PromptCompiler, file_source, inline_source
from aimedia.domain import PromptRequiredError, UnsupportedInputFormatError
from aimedia.logging import EventLogger

SECRET_TEXT = "SECRET-PROMPT-CANARY-9d3f"
SECRET_BYTES = b"SECRET-IMAGE-CANARY-1c7a"


def _logger() -> tuple[EventLogger, io.StringIO]:
    stream = io.StringIO()
    return EventLogger(stream=stream, min_level="DEBUG"), stream


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_prompt_compiled_logs_counts_without_prompt_text() -> None:
    logger, stream = _logger()

    PromptCompiler().compile([inline_source(SECRET_TEXT), inline_source("second")], logger=logger)

    records = _records(stream)
    assert [record["event"] for record in records] == ["prompt_compiled"]
    details = records[0]["details"]
    assert details["source_count"] == 2
    assert details["input_source_count"] == 2
    assert details["length_chars"] == len(f"{SECRET_TEXT}\n\nsecond")
    assert SECRET_TEXT not in stream.getvalue()


def test_input_prepared_logs_counts_and_sizes_without_paths(tmp_path: Path) -> None:
    logger, stream = _logger()
    first = tmp_path / "canary-ref-1.png"
    first.write_bytes(png_bytes())
    second = tmp_path / "canary-ref-2.jpg"
    second.write_bytes(jpeg_bytes())

    prepare_reference_images([first, second], logger=logger)

    records = _records(stream)
    assert [record["event"] for record in records] == ["input_prepared"]
    details = records[0]["details"]
    assert details["reference_count"] == 2
    assert details["total_size_bytes"] == len(png_bytes()) + len(jpeg_bytes())
    assert details["mime_types"] == ["image/jpeg", "image/png"]
    assert "canary-ref" not in stream.getvalue()


def test_validation_failed_logs_code_only() -> None:
    logger, stream = _logger()

    with pytest.raises(PromptRequiredError):
        PromptCompiler().compile([inline_source("   ")], logger=logger)

    records = _records(stream)
    assert [record["event"] for record in records] == ["validation_failed"]
    assert records[0]["level"] == "WARNING"
    assert records[0]["details"] == {"code": "PROMPT_REQUIRED"}
    assert SECRET_TEXT not in stream.getvalue()


def test_validation_failed_for_reference_logs_code_only(tmp_path: Path) -> None:
    logger, stream = _logger()
    broken = tmp_path / "secret-canary.png"
    broken.write_bytes(SECRET_BYTES)

    with pytest.raises(UnsupportedInputFormatError):
        prepare_reference_images([broken], logger=logger)

    records = _records(stream)
    assert [record["event"] for record in records] == ["validation_failed"]
    assert records[0]["details"] == {"code": "UNSUPPORTED_INPUT_FORMAT"}
    assert "secret-canary" not in stream.getvalue()
    assert SECRET_BYTES.decode() not in stream.getvalue()


def test_successful_preparation_does_not_emit_validation_failed(tmp_path: Path) -> None:
    """Успешная подготовка не пишет ложного отказа."""
    logger, stream = _logger()
    path = tmp_path / "ok.png"
    path.write_bytes(png_bytes())

    prepare_reference_images([path], limits=ReferenceLimits(max_references=1), logger=logger)

    events = [record["event"] for record in _records(stream)]
    assert events == ["input_prepared"]


def test_compilation_without_logger_is_silent() -> None:
    """Чистые преобразования не обязаны логировать и не падают без логгера."""
    prepared = PromptCompiler().compile([inline_source("A")])

    assert prepared.compiled.text == "A"


def test_prompt_file_path_is_not_logged(tmp_path: Path) -> None:
    """Путь prompt-файла в диагностику не попадает — только числа и длина."""
    logger, stream = _logger()
    source = tmp_path / "directory-that-should-stay-private" / "character.md"
    source.parent.mkdir()
    source.write_text("A", encoding="utf-8")

    PromptCompiler().compile([file_source(source)], logger=logger)

    assert "character.md" not in stream.getvalue()
    assert "stay-private" not in stream.getvalue()
