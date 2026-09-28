"""Проверки самого контура quality: коды и обязательные suites."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_quality_module() -> object:
    spec = importlib.util.spec_from_file_location("quality", REPO_ROOT / "scripts" / "quality.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["quality"] = module
    spec.loader.exec_module(module)
    return module


quality = _load_quality_module()


def test_nonzero_child_code_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ненулевой код дочернего процесса становится итоговым кодом."""
    fake = quality.Check("fake", [sys.executable, "-c", "raise SystemExit(7)"])
    result = quality.run_check(fake, cwd=REPO_ROOT)
    assert result.exit_code == 7
    assert quality.overall_exit_code([result]) == 7


def test_later_success_does_not_mask_earlier_failure() -> None:
    failed = quality.CheckResult("fail", "x", exit_code=7, duration_ms=0, required=True)
    succeeded = quality.CheckResult("ok", "y", exit_code=0, duration_ms=0, required=True)
    assert quality.overall_exit_code([failed, succeeded]) == 7


def test_missing_required_suite_is_failure(tmp_path: Path) -> None:
    assert quality.find_missing_suites(tmp_path) == list(quality.REQUIRED_SUITES)


def test_present_required_suites_pass_check(tmp_path: Path) -> None:
    for suite in quality.REQUIRED_SUITES:
        (tmp_path / suite).mkdir(parents=True)
    assert quality.find_missing_suites(tmp_path) == []


def test_required_suites_exist_in_repo() -> None:
    assert quality.find_missing_suites(REPO_ROOT) == []


def test_run_check_uses_no_shell() -> None:
    import inspect

    source = inspect.getsource(quality.run_check)
    assert "shell=False" in source
    assert "shell=True" not in source
