"""Subprocess-проверки запуска CLI без ключа, БД и сети."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from offline_policy import child_process_env

TIMEOUT_SECONDS = 120


def _clean_env(cwd: Path) -> dict[str, str]:
    """Окружение CLI-ребёнка из общей политики.

    `child_process_env()` удаляет секреты (включая алиас из
    `AIMEDIA_POLZA_API_KEY_ENV`) и добавляет в `PYTHONPATH` каталог offline-guard,
    поэтому дочерний CLI тоже не выходит во внешнюю сеть.
    """
    env = child_process_env()
    for name in (
        "AIMEDIA_DATA_DIR",
        "AIMEDIA_LIVE_ENABLED",
        "AIMEDIA_LOG_LEVEL",
        "AIMEDIA_POLZA_API_KEY_ENV",
        "AIMEDIA_CONFIG",
    ):
        env.pop(name, None)
    # The explicit environment locator prevents reading installed user settings.
    # No config source is reported for this nonexistent file.
    env["AIMEDIA_CONFIG"] = str(cwd / "missing-settings.toml")
    return env


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aimedia", *args],
        cwd=str(cwd),
        env=_clean_env(cwd),
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
    env = _clean_env(tmp_path)
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


def test_help_emits_no_diagnostics(tmp_path: Path) -> None:
    """Обычная справка не производит диагностического шума в stderr."""
    result = _run(["--help"], cwd=tmp_path)
    assert result.returncode == 0
    assert result.stderr.strip() == ""


def test_version_json_keeps_diagnostics_off_stdout(tmp_path: Path) -> None:
    """В `--json` stdout — один документ, диагностика уходит в stderr."""
    result = _run(["version", "--json"], cwd=tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True

    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    names = [event["event"] for event in events]
    assert "app_started" in names
    assert "config_loaded" in names
    assert "command_finished" in names
    config_event = next(event for event in events if event["event"] == "config_loaded")
    assert config_event["details"]["sources"] == ["env", "default"]
    assert events[-1]["details"]["exit_code"] == 0


def test_version_json_stderr_has_no_api_key(tmp_path: Path) -> None:
    """Даже при экспортированном ключе диагностика не печатает его значение."""
    env = _clean_env(tmp_path)
    env["POLZA_API_KEY"] = "sk-canary-0123456789abcdef"
    result = subprocess.run(
        [sys.executable, "-m", "aimedia", "version", "--json"],
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
    assert env["POLZA_API_KEY"] not in result.stderr
    assert env["POLZA_API_KEY"] not in result.stdout


# Синтетический canary. Он не является реальным ключом и служит только целью
# проверки: секрет, ошибочно переданный в значение настройки, не должен утечь.
CANARY_SECRET = "sk-canary-e01-diagnostics-0123456789"


def test_invalid_log_level_as_env_is_safe_config_error(tmp_path: Path) -> None:
    """Canary в `AIMEDIA_LOG_LEVEL` даёт код `4`, а не трассировку с секретом.

    Ошибка настроек возникает до настройки логгера; если пробросить её сырой,
    rich напечатает трассировку, включающую недопустимое значение. Контракт
    baseline E00: конфигурационная ошибка — код `4`, stdout не засорён.
    """
    env = _clean_env(tmp_path)
    env["AIMEDIA_LOG_LEVEL"] = CANARY_SECRET
    result = subprocess.run(
        [sys.executable, "-m", "aimedia", "version", "--json"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert CANARY_SECRET not in result.stdout
    assert CANARY_SECRET not in result.stderr
    assert "Traceback" not in result.stderr
    assert "log_level" in payload["error"]["message"]


def test_invalid_log_level_as_option_is_safe_config_error(tmp_path: Path) -> None:
    """Canary в значении `--log-level` также не отражается в выводе."""
    result = _run(["--log-level", CANARY_SECRET, "version", "--json"], cwd=tmp_path)
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIGURATION_ERROR"
    assert CANARY_SECRET not in result.stdout
    assert CANARY_SECRET not in result.stderr
    assert "Traceback" not in result.stderr


def test_app_started_omits_option_values(tmp_path: Path) -> None:
    """`app_started` логирует только путь команды и не дублирует значения опций.

    Сырой `sys.argv` содержал бы значение `--data-dir`, и в него мог бы попасть
    ключ. Диагностика должна содержать нормализованное имя команды, а само
    значение остаться только в фактически переданной опции.
    """
    secret_path = str(tmp_path / CANARY_SECRET)
    result = _run(["--data-dir", secret_path, "version", "--json"], cwd=tmp_path)
    assert result.returncode == 0
    events = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    started = next(event for event in events if event["event"] == "app_started")
    assert CANARY_SECRET not in json.dumps(started, ensure_ascii=False)
    assert started["command"].endswith("version")
    assert "aimedia" in started["command"]
    assert CANARY_SECRET not in result.stderr
    assert CANARY_SECRET not in result.stdout
