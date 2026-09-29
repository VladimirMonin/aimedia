"""Безопасные события artifacts: converted/saved/cleanup и public download helpers.

События E05 (`docs/plans/README.md`): `artifact_download_started`,
`artifact_download_failed`, `artifact_converted`, `artifact_saved`,
`artifact_cleanup_failed`. Проверяется не только факт записи, но и её границы: в
событии есть локальный ID, enum роли/формата и числовые размеры, но нет raw URL,
путей, имён файлов, текста prompt и текста исключения.

Все изображения синтетические, файлы живут в `tmp_path`; реальная сеть и
пользовательский data-root не используются. Отдельно проверяется, что best-effort
диагностика не превращает успешную публикацию в отказ с orphan-файлом.
"""

from __future__ import annotations

import errno
import io
import json
from pathlib import Path

import pytest
from image_fixtures import png_bytes

from aimedia.artifacts import (
    PillowArtifactStorage,
    log_artifact_cleanup_failed,
    log_artifact_converted,
    log_artifact_download_failed,
    log_artifact_download_started,
    log_artifact_saved,
    output,
)
from aimedia.artifacts.image import ImageConversionError
from aimedia.domain import ArtifactRole, FinalFormat
from aimedia.logging import EVENT_FIELDS, EventLogger, correlation

# Canary-значения: если они появятся в записи, границы события нарушены.
CANARY_PATH = Path("private-canary-dir") / "secret-artifact.png"
CANARY_BASENAME = "secret-canary-basename"
CANARY_URL = "https://cdn.example.org/secret/artifact.png?token=SECRET-QUERY-TOKEN-4a2b"
CANARY_PROMPT = "SECRET-PROMPT-CANARY-7c1d"
CANARY_EXCEPTION = "SECRET-EXCEPTION-CANARY-3e9f"


def _logger() -> tuple[EventLogger, io.StringIO]:
    stream = io.StringIO()
    return EventLogger(stream=stream, min_level="DEBUG"), stream


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _events(stream: io.StringIO) -> list[str]:
    return [str(record["event"]) for record in _records(stream)]


def _details(stream: io.StringIO, event: str) -> dict[str, object]:
    """Details единственной записи события: его отсутствие — ошибка теста."""
    matches = [record for record in _records(stream) if record["event"] == event]
    assert len(matches) == 1, f"ожидалась ровно одна запись {event}: {_records(stream)}"
    return dict(matches[0].get("details") or {})


def _assert_allowed_fields(stream: io.StringIO) -> None:
    """Каждая запись состоит только из разрешённых полей контракта логирования."""
    for record in _records(stream):
        assert set(record) <= set(EVENT_FIELDS), record


class _SavedTimingStream(io.StringIO):
    """Стрим, фиксирующий, существовал ли файл в момент записи `artifact_saved`.

    Проверка выносится из `write`, чтобы best-effort-обёртка storage не могла
    проглотить провал проверки вместе с собственным отказом обработчика.
    """

    def __init__(self, expected: Path) -> None:
        super().__init__()
        self._expected = expected
        self.file_present_at_saved: list[bool] = []

    def write(self, text: str) -> int:
        if '"event": "artifact_saved"' in text:
            self.file_present_at_saved.append(self._expected.exists())
        return super().write(text)


class _ExplodingStream(io.StringIO):
    """Обработчик, который всегда падает: имитация сбоя диагностического канала."""

    def write(self, text: str) -> int:
        raise RuntimeError(f"diagnostics handler failed {CANARY_EXCEPTION}")


def test_saved_event_reports_counts_without_path_or_basename(tmp_path: Path) -> None:
    """`artifact_saved` несёт роль/формат/размеры, но не путь и не имя файла."""
    source = png_bytes(width=4, height=3)
    expected = tmp_path / "outputs" / "7" / f"{CANARY_BASENAME}.png"
    stream = _SavedTimingStream(expected)
    storage = PillowArtifactStorage(
        data_root=tmp_path, logger=EventLogger(stream=stream, min_level="DEBUG")
    )

    artifact = storage.save(
        job_id=7, content=source, final_format=FinalFormat.PNG, base_name=CANARY_BASENAME
    )

    # Файл действительно опубликован и описывается возвращённым Artifact.
    assert expected.read_bytes() == source
    assert storage.exists(artifact) is True
    # `artifact_saved` записан уже после появления файла на диске.
    assert stream.file_present_at_saved == [True]
    assert _events(stream) == ["artifact_saved"]
    record = _records(stream)[0]
    assert record["job_id"] == "7"
    assert record["details"] == {
        "role": "final",
        "final_format": "png",
        "size_bytes": len(source),
        "width": 4,
        "height": 3,
    }
    # Ни имя файла, ни путь, ни имя managed-каталога в запись не попали.
    output_text = stream.getvalue()
    assert CANARY_BASENAME not in output_text
    assert str(CANARY_PATH) not in output_text
    assert str(tmp_path) not in output_text
    assert "outputs" not in output_text
    _assert_allowed_fields(stream)


def test_converted_event_only_for_actual_conversion(tmp_path: Path) -> None:
    """`artifact_converted` пишется только при фактической перекодировке."""
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)

    storage.save(job_id=1, content=png_bytes(width=4, height=3), final_format=FinalFormat.JPEG)

    assert _events(stream) == ["artifact_converted", "artifact_saved"]
    converted = _records(stream)[0]
    assert converted["job_id"] == "1"
    assert converted["details"] == {
        "role": "final",
        "source_format": "png",
        "final_format": "jpeg",
        "width": 4,
        "height": 3,
    }
    _assert_allowed_fields(stream)


def test_no_converted_event_when_bytes_are_saved_as_is(tmp_path: Path) -> None:
    """Сохранение валидных байтов в том же формате и ORIGINAL не логируют конвертацию."""
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)
    source = png_bytes()

    storage.save(job_id=1, content=source, final_format=FinalFormat.PNG)
    storage.save(job_id=2, content=source, role=ArtifactRole.ORIGINAL)

    assert _events(stream) == ["artifact_saved", "artifact_saved"]
    assert "artifact_converted" not in _events(stream)
    _assert_allowed_fields(stream)


def test_cleanup_failed_event_only_when_temp_unlink_signaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`artifact_cleanup_failed` пишется по сигналу публикации, но без пути."""
    original_unlink = Path.unlink

    def refuse_temp_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.suffix == ".part":
            raise PermissionError(f"temporary cleanup denied {CANARY_EXCEPTION}")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_temp_unlink)
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)

    artifact = storage.save(job_id=6, content=png_bytes(), final_format=FinalFormat.PNG)

    assert _events(stream) == ["artifact_saved", "artifact_cleanup_failed"]
    assert _records(stream)[1]["level"] == "WARNING"
    assert _details(stream, "artifact_cleanup_failed") == {"role": "final"}
    output_text = stream.getvalue()
    assert ".part" not in output_text
    assert ".aimedia-" not in output_text
    assert CANARY_EXCEPTION not in output_text
    # Опубликованный результат всё равно существует.
    assert storage.exists(artifact) is True
    _assert_allowed_fields(stream)


def test_no_cleanup_event_when_temp_file_removed(tmp_path: Path) -> None:
    """Успешная публикация без сигнала cleanup не пишет ложно `artifact_cleanup_failed`."""
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)

    storage.save(job_id=1, content=png_bytes(), final_format=FinalFormat.PNG)

    assert _events(stream) == ["artifact_saved"]
    assert "artifact_cleanup_failed" not in _events(stream)


@pytest.mark.parametrize("failure", ["conversion", "write"])
def test_no_events_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Отказ конвертации/записи не оставляет ложных событий."""
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)

    if failure == "conversion":
        with pytest.raises(ImageConversionError):
            storage.save(job_id=1, content=b"not an image at all", final_format=FinalFormat.PNG)
    else:

        def disk_full(fd: int, data: object) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(output.os, "write", disk_full)
        with pytest.raises(OSError):
            storage.save(job_id=1, content=png_bytes(), final_format=FinalFormat.PNG)

    assert _records(stream) == []


def test_logger_none_returns_artifact_without_events(tmp_path: Path) -> None:
    """Отсутствующий логгер (обратная совместимость) не меняет результат save."""
    storage = PillowArtifactStorage(data_root=tmp_path)

    artifact = storage.save(job_id=3, content=png_bytes(), final_format=FinalFormat.PNG)

    assert artifact.local_path == Path("outputs/3/result.png")
    assert (tmp_path / "outputs" / "3" / "result.png").exists()
    assert storage.exists(artifact) is True


def test_download_helpers_are_silent_without_logger(tmp_path: Path) -> None:
    """Public download helpers без логгера — no-op и для недоверенного ввода."""
    assert log_artifact_download_started(None, job_id=1, role=ArtifactRole.FINAL) is None
    assert log_artifact_download_failed(None, job_id=1, role=ArtifactRole.FINAL) is None
    assert log_artifact_download_started(None, job_id=CANARY_PROMPT, role=CANARY_PROMPT) is None


def test_logger_failure_does_not_fail_save_and_keeps_published_artifact(
    tmp_path: Path,
) -> None:
    """Сбой диагностики после публикации не превращает save в отказ с orphan-файлом."""
    storage = PillowArtifactStorage(
        data_root=tmp_path,
        logger=EventLogger(stream=_ExplodingStream(), min_level="DEBUG"),
    )

    artifact = storage.save(job_id=9, content=png_bytes(), final_format=FinalFormat.PNG)

    assert artifact.local_path == Path("outputs/9/result.png")
    assert (tmp_path / "outputs" / "9" / "result.png").exists()
    assert storage.exists(artifact) is True


def test_logger_failure_during_conversion_still_returns_converted_artifact(tmp_path: Path) -> None:
    """Даже при фактической конвертации сбой канала не подменяет успешный save."""
    storage = PillowArtifactStorage(
        data_root=tmp_path,
        logger=EventLogger(stream=_ExplodingStream(), min_level="DEBUG"),
    )

    artifact = storage.save(job_id=2, content=png_bytes(), final_format=FinalFormat.WEBP)

    assert artifact.local_path == Path("outputs/2/result.webp")
    assert storage.resolve_path(artifact).exists() is True


def test_download_helpers_report_role_without_url_or_correlation(tmp_path: Path) -> None:
    """Public download helpers несут роль/ID, но не raw URL и не child/context."""
    logger, stream = _logger()
    child = logger.child(
        command=CANARY_URL,
        provider=CANARY_PROMPT,
        remote_operation=CANARY_EXCEPTION,
        remote_job_id=CANARY_URL,
    )
    with correlation(command=CANARY_PROMPT, remote_job_id=CANARY_URL):
        log_artifact_download_started(child, job_id=5, role=ArtifactRole.FINAL)
        log_artifact_download_failed(child, job_id=5, role="original")

    assert _events(stream) == ["artifact_download_started", "artifact_download_failed"]
    records = _records(stream)
    assert records[0]["job_id"] == "5"
    assert records[0]["details"] == {"role": "final"}
    assert records[1]["level"] == "ERROR"
    assert records[1]["details"] == {"role": "original"}
    assert all(
        "command" not in record
        and "provider" not in record
        and "remote_operation" not in record
        and "remote_job_id" not in record
        for record in records
    )
    output_text = stream.getvalue()
    assert CANARY_URL not in output_text
    assert CANARY_PROMPT not in output_text
    assert CANARY_EXCEPTION not in output_text
    _assert_allowed_fields(stream)


def test_direct_helpers_reject_tainted_or_non_positive_values_without_records() -> None:
    """Аннотации не заменяют runtime-проверку: недоверенный ввод не пишется."""
    logger, stream = _logger()
    base = {
        "job_id": 1,
        "role": "final",
        "final_format": "png",
        "size_bytes": 1,
        "width": 1,
        "height": 1,
    }

    with pytest.raises(ValueError):
        log_artifact_saved(logger, **{**base, "job_id": CANARY_PROMPT})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        log_artifact_saved(logger, **{**base, "role": CANARY_PROMPT})
    with pytest.raises(ValueError):
        log_artifact_saved(
            logger,
            **{**base, "final_format": CANARY_PATH.as_posix()},  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        log_artifact_saved(logger, **{**base, "size_bytes": True})
    with pytest.raises(ValueError):
        log_artifact_saved(logger, **{**base, "job_id": 0})
    with pytest.raises(ValueError):
        log_artifact_saved(logger, **{**base, "job_id": -1})
    with pytest.raises(ValueError):
        log_artifact_converted(
            logger,
            job_id=1,
            role="final",
            source_format="png",
            final_format="jpeg",
            width=-1,
            height=1,
        )

    assert _records(stream) == []


def test_helper_rejection_text_does_not_echo_tainted_value() -> None:
    """Текст отказа не повторяет недоверенное значение (даже в трассировке)."""
    logger, stream = _logger()

    with pytest.raises(ValueError) as caught:
        log_artifact_cleanup_failed(logger, job_id=1, role=CANARY_PATH.as_posix())

    assert _records(stream) == []
    assert CANARY_PATH.as_posix() not in str(caught.value)


def test_storage_does_not_emit_download_events_on_delivered_bytes(tmp_path: Path) -> None:
    """Файловый adapter получает готовые байты и download-события не пишет."""
    logger, stream = _logger()
    storage = PillowArtifactStorage(data_root=tmp_path, logger=logger)

    storage.save(job_id=4, content=png_bytes(), final_format=FinalFormat.JPEG)

    events = _events(stream)
    assert "artifact_download_started" not in events
    assert "artifact_download_failed" not in events
    assert events == ["artifact_converted", "artifact_saved"]
