"""Publication and artifact-save failures leave only caller-owned preexisting files."""

import errno
import hashlib
import os
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from aimedia.artifacts import PillowArtifactStorage, output, publish_output
from aimedia.artifacts.image import ImageConversionError
from aimedia.domain import ArtifactRole, FinalFormat


def test_interrupted_partial_write_does_not_publish(tmp_path, monkeypatch):
    original_write = os.write
    calls = 0

    def interrupted_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, data[:2])
        raise InterruptedError("interrupted write")

    monkeypatch.setattr(output.os, "write", interrupted_write)
    with pytest.raises(InterruptedError):
        publish_output(b"verified", tmp_path, "image", "png", managed=False)
    assert calls == 2
    assert not list(tmp_path.iterdir())


def test_disk_full_after_write_does_not_publish(tmp_path, monkeypatch):
    def disk_full(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(output.os, "fsync", disk_full)
    with pytest.raises(OSError) as exc:
        publish_output(b"verified", tmp_path, "image", "png", managed=True)
    assert exc.value.errno == errno.ENOSPC
    assert not list(tmp_path.iterdir())


def test_temp_cleanup_failure_after_link_returns_published_metadata(tmp_path, monkeypatch):
    original_unlink = Path.unlink
    attempted = []

    def refuse_temp_unlink(path, *args, **kwargs):
        if path.suffix == ".part":
            attempted.append(path)
            raise PermissionError("temporary cleanup denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_temp_unlink)
    result = publish_output(b"verified", tmp_path, "image", "png", managed=True)

    assert result.path == tmp_path / "image.png"
    assert result.path.read_bytes() == b"verified"
    assert result.size_bytes == len(b"verified")
    assert result.sha256 == hashlib.sha256(b"verified").hexdigest()
    assert result.ownership == "managed"
    assert attempted == [result.cleanup_failed_temp_path]
    assert result.cleanup_failed_temp_path is not None
    assert result.cleanup_failed_temp_path.read_bytes() == b"verified"
    assert set(tmp_path.iterdir()) == {result.path, result.cleanup_failed_temp_path}


@pytest.mark.parametrize("failure_point", ["write", "fsync"])
def test_temp_cleanup_failure_preserves_publication_error(tmp_path, monkeypatch, failure_point):
    original_unlink = Path.unlink
    attempted = []
    original_error = OSError(errno.ENOSPC, "disk full")

    def fail_operation(*args):
        raise original_error

    def refuse_temp_unlink(path, *args, **kwargs):
        if path.suffix == ".part":
            attempted.append(path)
            raise PermissionError("temporary cleanup denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(output.os, failure_point, fail_operation)
    monkeypatch.setattr(Path, "unlink", refuse_temp_unlink)
    with pytest.raises(OSError) as exc:
        publish_output(b"verified", tmp_path, "image", "png", managed=False)

    assert exc.value is original_error
    assert exc.value.errno == errno.ENOSPC
    assert exc.value.__notes__ == ["Temporary output cleanup failed; a temporary file may remain"]
    assert len(attempted) == 1
    assert attempted[0].parent == tmp_path
    assert attempted[0].exists()
    assert not (tmp_path / "image.png").exists()


def test_unavailable_exclusive_link_refuses_without_fallback(tmp_path, monkeypatch):
    existing = tmp_path / "image.png"
    existing.write_bytes(b"user file")

    def unsupported_link(source, destination):
        raise OSError(errno.EOPNOTSUPP, "Hard links unavailable")

    monkeypatch.setattr(output.os, "link", unsupported_link)
    with pytest.raises(OSError) as exc:
        publish_output(b"verified", tmp_path, "image", "png", managed=False)
    assert exc.value.errno == errno.EOPNOTSUPP
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"user file"


def _valid_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 3), (12, 34, 56)).save(buffer, "PNG")
    return buffer.getvalue()


def test_save_rejects_non_image_without_artifact_or_file(tmp_path):
    storage = PillowArtifactStorage(data_root=tmp_path)

    with pytest.raises(ImageConversionError):
        storage.save(job_id=1, content=b"not an image at all", final_format=FinalFormat.PNG)

    assert not (tmp_path / "outputs").exists()


def test_save_original_role_rejects_non_image_without_artifact(tmp_path):
    storage = PillowArtifactStorage(data_root=tmp_path)

    with pytest.raises(ImageConversionError):
        storage.save(job_id=1, content=b"garbage bytes", role=ArtifactRole.ORIGINAL)

    assert not (tmp_path / "outputs").exists()


def test_save_write_failure_leaves_no_published_file(tmp_path, monkeypatch):
    storage = PillowArtifactStorage(data_root=tmp_path)

    def disk_full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(output.os, "write", disk_full)
    with pytest.raises(OSError) as exc:
        storage.save(job_id=1, content=_valid_png(), final_format=FinalFormat.PNG)

    assert exc.value.errno == errno.ENOSPC
    managed_dir = tmp_path / "outputs" / "1"
    assert managed_dir.is_dir()
    assert list(managed_dir.iterdir()) == []
