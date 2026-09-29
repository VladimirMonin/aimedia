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
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

# Общая политика изоляции живёт рядом с тестами. Единственный владелец правил —
# `tests/offline/offline_policy.py`; harness загружает её по пути, потому что
# запускается обычным Python-процессом, а не через pytest.
OFFLINE_POLICY_PATH = REPO_ROOT / "tests" / "offline" / "offline_policy.py"

# Разбор итоговой строки pytest `--collect-only -q`: `28 tests collected in 0.2s`
# или `no tests collected in 0.01s`.
_COLLECTED_RE = re.compile(r"^(?:(\d+)\s+tests?\s+collected|no\s+tests?\s+collected)", re.MULTILINE)

# Обязательные suites на текущем этапе. Список расширяется вместе с появлением
# реальных каталогов тестов, а не заранее.
REQUIRED_SUITES: tuple[str, ...] = (
    "tests/unit",
    "tests/architecture",
    "tests/cli",
    "tests/tooling",
    "tests/security",
    "tests/contracts",
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
    # Сколько тестов реально собрал `--collect-only`. `None` — проверка не
    # собирала тесты.
    collected_tests: int | None = None


@dataclass
class RunReport:
    mode: str
    exit_code: int
    results: list[CheckResult] = field(default_factory=list)


def load_offline_policy() -> ModuleType:
    """Загрузить общую offline-политику из `tests/offline/offline_policy.py`."""
    spec = importlib.util.spec_from_file_location("offline_policy", OFFLINE_POLICY_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - защита от повреждения репо
        raise RuntimeError(f"Не удалось загрузить offline-политику: {OFFLINE_POLICY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["offline_policy"] = module
    spec.loader.exec_module(module)
    return module


offline_policy = load_offline_policy()


def find_missing_suites(root: Path, suites: tuple[str, ...] = REQUIRED_SUITES) -> list[str]:
    """Вернуть обязательные suites, которых нет в рабочем дереве."""
    return [suite for suite in suites if not (root / suite).is_dir()]


def sanitize_process_env() -> tuple[str, ...]:
    """Удалить секреты из окружения harness до запуска любых дочерних проверок.

    Возвращает только имена удалённых переменных: их безопасно печатать, значения
    не читаются и не логируются.
    """
    return offline_policy.scrub_secret_env(os.environ)


def run_check(check: Check, *, cwd: Path) -> CheckResult:
    """Запустить команду без shell и вернуть её сырой exit code.

    Окружение ребёнка строится через общую политику: секреты удалены, а
    `PYTHONPATH` дополнен каталогом offline-guard, поэтому Python-потомки
    проверок тоже запрещают внешнюю сеть.
    """
    started = time.monotonic()
    completed = subprocess.run(
        check.args,
        cwd=str(cwd),
        env=offline_policy.child_process_env(os.environ),
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


def _collect_args(suite: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        suite,
        "--collect-only",
        "-q",
        "-m",
        "not live",
        "--strict-markers",
    ]


def parse_collected_tests(stdout: str) -> int | None:
    """Число собранных тестов из вывода `pytest --collect-only -q`.

    Возвращает `None`, если итоговая строка не распознана: непонятный вывод не
    считается доказательством наличия тестов.
    """
    matches = list(_COLLECTED_RE.finditer(stdout))
    if not matches:
        return None
    digits = matches[-1].group(1)
    return int(digits) if digits is not None else 0


def run_suite_collect_check(suite: str, *, cwd: Path) -> CheckResult:
    """Проверить, что обязательный suite собирает хотя бы один тест.

    Общий pytest-запуск всех suites маскирует пустой каталог: пока другой suite
    собирает тесты, общий код успешен. Поэтому каждый обязательный suite
    проверяется отдельно, а ноль собранных тестов даёт failure с
    `MISSING_SUITE_EXIT_CODE`, а не зелёный итог.
    """
    check = Check(f"collect:{suite}", _collect_args(suite))
    result = run_check(check, cwd=cwd)
    collected = parse_collected_tests(result.stdout)
    result.collected_tests = collected
    if collected is None or collected == 0:
        # Ноль собранных тестов — отсутствие suite, даже если pytest вернул 5.
        result.exit_code = MISSING_SUITE_EXIT_CODE
        result.required = True
        detail = f"Suite {suite} не собрал ни одного теста (collected={collected!r})."
        result.stderr = (result.stderr + "\n" + detail).strip()
    return result


def run_collect_gate(*, cwd: Path) -> list[CheckResult]:
    """Отдельно проверить собираемость каждого обязательного suite."""
    return [run_suite_collect_check(suite, cwd=cwd) for suite in REQUIRED_SUITES]


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

    sanitize_process_env()
    results.extend(run_collect_gate(cwd=root))
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
