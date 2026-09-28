"""Проверки единой версии пакета."""

from __future__ import annotations

import tomllib
from pathlib import Path

from aimedia import __version__, get_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_matches_pyproject() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert get_version() == project["project"]["version"]


def test_version_is_not_empty() -> None:
    assert __version__
