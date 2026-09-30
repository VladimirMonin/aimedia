"""CN-01: managed reference copies; no conversion, DB, source reread or cleanup."""

from __future__ import annotations

import os
import re
from pathlib import Path

from aimedia.application.inputs.prepare import validate_reference_content
from aimedia.artifacts.output import publish_output
from aimedia.domain.inputs import InputRef

_EXTENSION_BY_MIME = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}


class LocalManagedInputStorage:
    """Scoped adapter of ManagedInputStorage, using the E05 publisher.

    Existing ancestor redirects are rejected before mkdir/read. As with E05,
    hostile concurrent ancestor replacement after preflight is not guarded.
    Published files are never deleted, even if a later check/DB write fails.
    """

    def __init__(self, *, data_root: Path) -> None:
        self._data_root = Path(os.path.abspath(data_root))

    def save(self, *, job_id: int, ref: InputRef, content: bytes) -> InputRef:
        if type(job_id) is not int or job_id <= 0:
            raise ValueError("Managed input requires a positive job_id")
        if type(ref.position) is not int or ref.position < 0 or ref.managed_path is not None:
            raise ValueError("Managed input requires an unarchived ordered reference")
        validate_reference_content(ref, content)
        extension = _EXTENSION_BY_MIME[ref.mime_type or ""]
        directory = self._data_root / "inputs" / str(job_id)
        self._reject_redirects(directory)
        directory.mkdir(parents=True, exist_ok=True)
        published = publish_output(content, directory, str(ref.position), extension, managed=True)
        copied = InputRef.model_validate(
            {**ref.model_dump(), "managed_path": published.path.relative_to(self._data_root)}
        )
        # Actual published bytes, not merely the attempted write, must match.
        self.resolve_path(copied)
        return copied

    def resolve_path(self, ref: InputRef) -> Path:
        # Revalidate even a model_copy/model_construct supplied by a caller.
        ref = InputRef.model_validate(ref.model_dump())
        path = ref.managed_path
        if path is None:
            raise ValueError("Reference has no managed copy")
        parts = path.parts
        extension = _EXTENSION_BY_MIME.get(ref.mime_type or "")
        if (
            len(parts) != 3
            or parts[0] != "inputs"
            or re.fullmatch(r"[1-9][0-9]*", parts[1]) is None
            or extension is None
            or ref.position < 0
            or re.fullmatch(rf"{ref.position}(?:_[0-9]{{3,}})?\.{extension}", parts[2]) is None
        ):
            raise ValueError("Invalid managed reference path")
        candidate = self._data_root / path
        self._reject_redirects(candidate)
        if not candidate.is_file():
            raise ValueError("Managed reference copy is missing")
        assert ref.size_bytes is not None
        if candidate.stat().st_size != ref.size_bytes:
            raise ValueError("Managed reference copy size mismatch")
        # Bound allocation to the recorded size even if the file grows after stat.
        with candidate.open("rb") as stream:
            content = stream.read(ref.size_bytes + 1)
        validate_reference_content(ref, content)
        return candidate

    @staticmethod
    def _reject_redirects(path: Path) -> None:
        for component in (path, *path.parents):
            if component.is_symlink() or component.is_junction():
                raise ValueError("Symlinked or junction managed input path")
