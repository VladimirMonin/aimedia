"""Subprocess-проверки запуска CLI без ключа, БД и сети."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TIMEOUT_SECONDS = 120


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("POLZA_API_KEY", "AIMEDIA_DATA_DIR", "AIMEDIA_LIVE_ENABLED"):
        env.pop(name, None)
    return env


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aimedia", *args],
        cwd=str(cwd),
        env=_clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=TIMEOUT_SECONDS,
    )


def test_help_runs_without_key(tmp_path: Path) -> None:
    result = _run(["--help"], cwd=tmp_path)
    assert result.returncode == 0
    assert "aimedia" in result.stdout.lower()
    assert "version" in result.stdout


def test_version_json_is_single_document(tmp_path: Path) -> None:
    result = _run(["version", "--json"], cwd=tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["version"]


def test_version_human(tmp_path: Path) -> None:
    result = _run(["version"], cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip().startswith("aimedia ")


def test_help_does_not_create_user_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "user-data"
    env = _clean_env()
    env["AIMEDIA_DATA_DIR"] = str(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "aimedia", "--help"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 0
    assert not data_dir.exists()


@pytest.mark.parametrize("args", [["version", "--json"], ["--help"]])
def test_cli_reports_no_ansi(tmp_path: Path, args: list[str]) -> None:
    result = _run(args, cwd=tmp_path)
    assert result.returncode == 0
    assert "\x1b[" not in result.stdout
