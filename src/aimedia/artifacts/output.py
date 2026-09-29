"""No-clobber публикация уже проверенных байтов; декодирование изображений — не здесь."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Ownership = Literal["managed", "user_output"]
_ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL"})
_RESERVED_DEVICE = re.compile(r"(?:COM|LPT)[1-9\u00b9\u00b2\u00b3]$", re.IGNORECASE)
_INVALID_NAME_CHARACTERS = frozenset('/\\<>:"|?*')


@dataclass(frozen=True, slots=True)
class PublishedOutput:
    path: Path
    size_bytes: int
    sha256: str
    ownership: Ownership
    # Internal cleanup signal: the caller owns retry/diagnostics for this temp file.
    cleanup_failed_temp_path: Path | None = None


def _validate_filename(name: str, extension: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or name[-1] in {" ", "."}
        or any(
            char in _INVALID_NAME_CHARACTERS or unicodedata.category(char) == "Cc" for char in name
        )
        or "." in name
        or name.upper() in _RESERVED_NAMES
        or _RESERVED_DEVICE.fullmatch(name) is not None
    ):
        raise ValueError("Unsafe output basename")
    if extension not in _ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported output extension")


def _output_directory(directory: Path) -> Path:
    # Reject existing reparse redirects; hostile concurrent directory replacement
    # between preflight and path-based publication is outside this adapter's threat model.
    absolute = Path(os.path.abspath(directory))
    for component in (absolute, *absolute.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("Symlinked or junction output directory")
    if not absolute.is_dir():
        raise ValueError("Output directory must already exist")
    return absolute


def publish_output(
    data: bytes,
    directory: Path,
    name: str,
    extension: str,
    *,
    managed: bool,
) -> PublishedOutput:
    """Publish verified final bytes exclusively; never overwrite or delete a destination.

    The caller must verify/decode/convert before calling. An existing output directory
    is required. Hard-linking a flushed temp file is the no-clobber atomic primitive;
    if the filesystem cannot provide it, publication fails without a rename fallback.
    Existing symlink/junction directory components are rejected; a hostile process
    replacing an ancestor after preflight is not guarded against on Windows.
    """
    _validate_filename(name, extension)
    target_dir = _output_directory(directory)
    fd, temp_name = tempfile.mkstemp(prefix=".aimedia-", suffix=".part", dir=target_dir)
    temp_path = Path(temp_name)
    try:
        digest = hashlib.sha256()
        view = memoryview(data)
        try:
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("Incomplete output write")
                digest.update(view[:count])
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)

        index = 1
        while True:
            filename = f"{name}{'' if index == 1 else f'_{index:03d}'}.{extension}"
            destination = target_dir / filename
            try:
                os.link(temp_path, destination)
            except FileExistsError:
                index += 1
                continue
            # Other errors (including unsupported hard links) must not trigger a
            # non-exclusive rename or copy fallback.
            break
    except BaseException as error:
        try:
            temp_path.unlink()
        except OSError:
            error.add_note("Temporary output cleanup failed; a temporary file may remain")
        raise

    try:
        temp_path.unlink()
    except OSError:
        cleanup_failed_temp_path: Path | None = temp_path
    else:
        cleanup_failed_temp_path = None
    return PublishedOutput(
        path=destination,
        size_bytes=len(data),
        sha256=digest.hexdigest(),
        ownership="managed" if managed else "user_output",
        cleanup_failed_temp_path=cleanup_failed_temp_path,
    )
