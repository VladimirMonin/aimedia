"""Local kernel-held Job locks: released by the OS on process exit/crash.

The small lock file is retained permanently; unlinking it would allow another
inode to be locked concurrently. No PID guessing, stale timeout or broker.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aimedia.storage.errors import StorageError


class JobOwnedError(StorageError):
    def __init__(self) -> None:
        super().__init__("Job уже исполняется другим локальным процессом")


@contextmanager
def claim_job(data_root: Path, job_id: int) -> Iterator[None]:
    if type(job_id) is not int or job_id <= 0:
        raise ValueError("Ownership requires a positive Job ID")
    directory = data_root.absolute() / "locks"
    for component in (directory, *directory.parents):
        if component.is_symlink() or component.is_junction():
            raise StorageError("Перенаправленный каталог ownership запрещён")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.lock"
    if path.is_symlink() or path.is_junction():
        raise StorageError("Перенаправленный файл ownership запрещён")
    # Never truncate: a Windows byte-range lock needs a stable first byte.
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise JobOwnedError() from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
