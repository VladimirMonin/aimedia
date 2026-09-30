"""Windows/POSIX kernel Job ownership, contention and process-crash release."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from offline_policy import child_process_env

from aimedia.storage.ownership import JobOwnedError, claim_job


def test_cross_process_owner_contention_and_crash_release(tmp_path: Path):
    script = """
import os, sys
from pathlib import Path
from aimedia.storage.ownership import claim_job
with claim_job(Path(sys.argv[1]), 12):
    print('OWNED', flush=True)
    sys.stdin.readline()
    os._exit(0)  # crash: no Python finally/unlock runs
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        env=child_process_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout.readline().strip() == "OWNED"
        with pytest.raises(JobOwnedError), claim_job(tmp_path, 12):
            pytest.fail("Second process acquired active Job")
        # Different Job ID does not block independent batch execution.
        with claim_job(tmp_path, 13):
            pass
        process.communicate("crash\n", timeout=10)
        assert process.returncode == 0
        with claim_job(tmp_path, 12):
            pass
        assert (tmp_path / "locks" / "12.lock").exists()
    finally:
        if process.poll() is None:
            process.communicate("crash\n", timeout=10)


def test_owner_rejects_invalid_identifier_without_files(tmp_path):
    with pytest.raises(ValueError), claim_job(tmp_path, True):
        pass
    assert not (tmp_path / "locks").exists()
