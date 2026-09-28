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


def test_parse_collected_tests_reads_pytest_summary() -> None:
    assert quality.parse_collected_tests("28 tests collected in 0.24s\n") == 28
    assert quality.parse_collected_tests("1 test collected in 0.02s\n") == 1
    assert quality.parse_collected_tests("no tests collected in 0.01s\n") == 0
    assert quality.parse_collected_tests("неожиданный вывод\n") is None


def test_empty_suite_within_collect_args_is_failure(tmp_path: Path) -> None:
    """Пустой каталог suite — failure, а не зелёный итог.

    `pytest` сам возвращает 5 на ноль собранных тестов, а harness дополнительно
    ставит `MISSING_SUITE_EXIT_CODE`, если ноль не отражён кодом.
    """
    (tmp_path / "tests" / "empty").mkdir(parents=True)
    result = quality.run_suite_collect_check("tests/empty", cwd=tmp_path)
    assert result.collected_tests == 0
    assert result.exit_code == quality.MISSING_SUITE_EXIT_CODE


def test_nonempty_suite_collect_check_passes(tmp_path: Path) -> None:
    suite = tmp_path / "tests" / "filled"
    suite.mkdir(parents=True)
    (suite / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = quality.run_suite_collect_check("tests/filled", cwd=tmp_path)
    assert result.collected_tests == 1
    assert result.exit_code == 0


def test_collect_gate_reports_every_required_suite(tmp_path: Path) -> None:
    """Общий pytest-запуск не маскирует пустой suite: он проверяется отдельно."""
    for suite in quality.REQUIRED_SUITES:
        (tmp_path / suite).mkdir(parents=True)
    results = quality.run_collect_gate(cwd=tmp_path)
    assert [result.name for result in results] == [
        f"collect:{suite}" for suite in quality.REQUIRED_SUITES
    ]
    assert all(result.required for result in results)
    assert all(result.exit_code == quality.MISSING_SUITE_EXIT_CODE for result in results)


def test_real_repo_collect_gate_passes() -> None:
    """Фактические обязательные suites репозитория собирают тесты."""
    results = quality.run_collect_gate(cwd=REPO_ROOT)
    assert len(results) == len(quality.REQUIRED_SUITES)
    for result in results:
        assert result.exit_code == 0, (result.name, result.stderr)
        assert result.collected_tests is not None and result.collected_tests > 0


def test_run_mode_fails_on_untouched_empty_suites(tmp_path: Path) -> None:
    """`run_mode` целиком отказывает, если обязательный suite пуст.

    Регресс на маскировку: общий pytest-запуск всех suites может быть зелёным за
    счёт соседнего каталога, а нулевой collect в одном suite обязан сделать режим
    неуспешным.
    """
    for suite in quality.REQUIRED_SUITES:
        (tmp_path / suite).mkdir(parents=True)

    report = quality.run_mode("quick", root=tmp_path)
    collect_results = [r for r in report.results if r.name.startswith("collect:")]
    assert collect_results
    assert quality.overall_exit_code(collect_results) == quality.MISSING_SUITE_EXIT_CODE
    assert report.exit_code != 0
