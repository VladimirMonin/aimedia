"""Publication failures leave only caller-owned preexisting output files."""

import errno
import hashlib
import os
from pathlib import Path

import pytest

from aimedia.artifacts import output, publish_output


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
