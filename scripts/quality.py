#!/usr/bin/env python
"""Единый контур проверок aimedia.

Режимы `quick`, `full` и `release` запускают обязательные проверки
последовательно, сохраняют stdout/stderr и **сырой** exit code каждой команды и
возвращают неуспех, если любая обязательная проверка провалилась.

Реализованы только те проверки и suites, которые существуют на текущем этапе.
Отсутствие обязательного набора тестов — failure, а не зелёный итог.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Обязательные suites на текущем этапе. Список расширяется вместе с появлением
# реальных каталогов тестов, а не заранее.
REQUIRED_SUITES: tuple[str, ...] = (
    "tests/unit",
    "tests/cli",
    "tests/tooling",
    "tests/security",
)

# Отдельный код возврата, когда обязательный набор тестов отсутствует.
MISSING_SUITE_EXIT_CODE = 66


@dataclass(frozen=True)
class Check:
    """Одна внешняя команда контура проверок."""

    name: str
    args: list[str]
    required: bool = True


@dataclass
class CheckResult:
    name: str
    command: str
    exit_code: int
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    required: bool = True


@dataclass
class RunReport:
    mode: str
    exit_code: int
    results: list[CheckResult] = field(default_factory=list)


def find_missing_suites(root: Path, suites: tuple[str, ...] = REQUIRED_SUITES) -> list[str]:
    """Вернуть обязательные suites, которых нет в рабочем дереве."""
    return [suite for suite in suites if not (root / suite).is_dir()]


def run_check(check: Check, *, cwd: Path) -> CheckResult:
    """Запустить команду без shell и вернуть её сырой exit code."""
    started = time.monotonic()
    completed = subprocess.run(
        check.args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    return CheckResult(
        name=check.name,
        command=" ".join(check.args),
        exit_code=completed.returncode,
        duration_ms=duration_ms,
        stdout=completed.stdout,
        stderr=completed.stderr,
        required=check.required,
    )


def run_checks(checks: list[Check], *, cwd: Path) -> list[CheckResult]:
    """Выполнить проверки последовательно, не останавливаясь на первом провале."""
    return [run_check(check, cwd=cwd) for check in checks]


def overall_exit_code(results: list[CheckResult]) -> int:
    """Вернуть сырой код первой провалившейся обязательной проверки.

    Неуспех обязательной проверки нельзя маскировать нулём: возвращается её
    фактический код, чтобы следующий элемент не считался успешным.
    """
    for result in results:
        if result.required and result.exit_code != 0:
            return result.exit_code
    return 0


def _pytest_args(extra: list[str] | None = None) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "pytest",
        *[f"{suite}" for suite in REQUIRED_SUITES],
        "-m",
        "not live",
        "--strict-markers",
    ]
    if extra:
        args.extend(extra)
    return args


def build_checks(mode: str) -> list[Check]:
    """Собрать список команд для режима."""
    checks: list[Check] = [
        Check("ruff-lint", [sys.executable, "-m", "ruff", "check", "."]),
        Check("ruff-format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
        Check("mypy", [sys.executable, "-m", "mypy", "src/aimedia"]),
    ]

    if mode == "quick":
        checks.append(Check("pytest", _pytest_args()))
    elif mode == "full":
        checks.append(
            Check(
                "pytest",
                _pytest_args(
                    [
                        "--cov=aimedia",
                        "--cov-branch",
                        "--cov-report=term-missing",
                    ]
                ),
            )
        )
    elif mode == "release":
        checks.append(
            Check(
                "pytest",
                _pytest_args(
                    [
                        "--cov=aimedia",
                        "--cov-branch",
                        "--cov-report=term-missing",
                    ]
                ),
            )
        )
        checks.append(Check("build", ["uv", "build"]))
    else:  # pragma: no cover - защита от неизвестного режима
        raise ValueError(f"Неизвестный режим: {mode}")
    return checks


def run_mode(mode: str, *, root: Path = REPO_ROOT, report_dir: Path | None = None) -> RunReport:
    """Выполнить режим и вернуть отчёт с итоговым exit code."""
    missing = find_missing_suites(root)
    results: list[CheckResult] = []
    if missing:
        results.append(
            CheckResult(
                name="required-suites",
                command="required-suites",
                exit_code=MISSING_SUITE_EXIT_CODE,
                duration_ms=0,
                stderr="Отсутствуют обязательные наборы тестов: " + ", ".join(missing),
                required=True,
            )
        )

    results.extend(run_checks(build_checks(mode), cwd=root))
    exit_code = overall_exit_code(results)
    report = RunReport(mode=mode, exit_code=exit_code, results=results)

    if report_dir is not None:
        _write_report(report, report_dir)
    return report


def _write_report(report: RunReport, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": report.mode,
        "exit_code": report.exit_code,
        "checks": [asdict(result) for result in report.results],
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stdout = "\n\n".join(
        f"=== {result.name} (exit {result.exit_code}) ===\n{result.stdout}"
        for result in report.results
    )
    stderr = "\n\n".join(
        f"=== {result.name} (exit {result.exit_code}) ===\n{result.stderr}"
        for result in report.results
    )
    (report_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (report_dir / "stderr.txt").write_text(stderr, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Контур проверок aimedia.")
    parser.add_argument("mode", choices=["quick", "full", "release"])
    parser.add_argument("--report-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    report = run_mode(args.mode, report_dir=args.report_dir)

    for result in report.results:
        status = "ok" if result.exit_code == 0 else f"FAIL({result.exit_code})"
        print(f"[{status}] {result.name}: {result.command}", file=sys.stderr)
        if result.exit_code != 0:
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
