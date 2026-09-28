"""Изоляция секретов от test import, collection и дочерних процессов.

Все значения — синтетические canary. Настоящий `.env` не читается: тесты
работают только с переменными, которые сами устанавливают.

Проверяется три слоя:

1. `tests/conftest.py` очищает окружение до collection и в каждом тесте;
2. `offline_policy.secret_env_names()` учитывает алиас из
   `AIMEDIA_POLZA_API_KEY_ENV`;
3. `scripts/quality.py` и `offline_policy.child_process_env()` не передают секреты
   ни одному дочернему процессу.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from offline_policy import API_KEY_ENV_LOCATOR, SECRET_ENV_VARS, child_process_env, secret_env_names

TIMEOUT_SECONDS = 60

REPO_ROOT = Path(__file__).resolve().parents[2]

CANARY_DEFAULT_NAME = "sk-canary-default-name-0123456789"
CANARY_ALIAS_NAME = "sk-canary-alias-name-0123456789"
CANARY_ALIAS_VAR = "AIMEDIA_TEST_ALIAS_KEY"


def _load_quality_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "quality_secret_probe", REPO_ROOT / "scripts" / "quality.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["quality_secret_probe"] = module
    spec.loader.exec_module(module)
    return module


def _load_conftest_fresh() -> object:
    """Импортировать `tests/conftest.py` заново, как при старте pytest."""
    spec = importlib.util.spec_from_file_location(
        "conftest_secret_probe", REPO_ROOT / "tests" / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["conftest_secret_probe"] = module
    spec.loader.exec_module(module)
    return module


def test_conftest_scrubs_secrets_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Импорт `conftest` очищает окружение до collection тестовых модулей.

    Это тот же момент, в который pytest подключает conftest: тестовый модуль и
    сам сбор тестов уже не видят значения ключей.
    """
    monkeypatch.setenv("POLZA_API_KEY", CANARY_DEFAULT_NAME)
    monkeypatch.setenv(API_KEY_ENV_LOCATOR, CANARY_ALIAS_VAR)
    monkeypatch.setenv(CANARY_ALIAS_VAR, CANARY_ALIAS_NAME)

    _load_conftest_fresh()

    assert "POLZA_API_KEY" not in os.environ
    assert CANARY_ALIAS_VAR not in os.environ
    assert SECRET_ENV_VARS == ("POLZA_API_KEY",)


def test_secret_env_names_includes_default_and_alias() -> None:
    names = secret_env_names({"AIMEDIA_POLZA_API_KEY_ENV": "CUSTOM_KEY", "CUSTOM_KEY": "x"})
    assert names == ("POLZA_API_KEY", "CUSTOM_KEY")


def test_secret_env_names_ignores_blank_alias() -> None:
    assert secret_env_names({"AIMEDIA_POLZA_API_KEY_ENV": "   "}) == ("POLZA_API_KEY",)


def test_child_process_env_drops_both_secret_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLZA_API_KEY", CANARY_DEFAULT_NAME)
    monkeypatch.setenv(API_KEY_ENV_LOCATOR, CANARY_ALIAS_VAR)
    monkeypatch.setenv(CANARY_ALIAS_VAR, CANARY_ALIAS_NAME)

    env = child_process_env()
    assert "POLZA_API_KEY" not in env
    assert CANARY_ALIAS_VAR not in env
    # Локатор не содержит значения ключа и переносится как есть.
    assert env[API_KEY_ENV_LOCATOR] == CANARY_ALIAS_VAR


def test_child_process_cannot_read_secret_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ребёнок не получает canary-значения ни под каким именем."""
    monkeypatch.setenv("POLZA_API_KEY", CANARY_DEFAULT_NAME)
    monkeypatch.setenv(API_KEY_ENV_LOCATOR, CANARY_ALIAS_VAR)
    monkeypatch.setenv(CANARY_ALIAS_VAR, CANARY_ALIAS_NAME)

    code = "import json, os\nprint(json.dumps({k: v for k, v in os.environ.items()}))\n"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=child_process_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    assert CANARY_DEFAULT_NAME not in completed.stdout
    assert CANARY_ALIAS_NAME not in completed.stdout


def test_quality_harness_scrubs_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """`quality.sanitize_process_env()` удаляет оба имени до запуска проверок."""
    quality = _load_quality_module()
    monkeypatch.setenv("POLZA_API_KEY", CANARY_DEFAULT_NAME)
    monkeypatch.setenv(API_KEY_ENV_LOCATOR, CANARY_ALIAS_VAR)
    monkeypatch.setenv(CANARY_ALIAS_VAR, CANARY_ALIAS_NAME)

    removed = quality.sanitize_process_env()
    assert "POLZA_API_KEY" in removed
    assert CANARY_ALIAS_VAR in removed
    assert "POLZA_API_KEY" not in os.environ
    assert CANARY_ALIAS_VAR not in os.environ
    # Возвращаются только имена, не значения.
    assert all(CANARY_DEFAULT_NAME not in name for name in removed)
    assert all(CANARY_ALIAS_NAME not in name for name in removed)


def test_quality_child_env_has_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверки harness получают окружение без секретов и с guard в PYTHONPATH."""
    quality = _load_quality_module()
    monkeypatch.setenv("POLZA_API_KEY", CANARY_DEFAULT_NAME)
    monkeypatch.setenv(API_KEY_ENV_LOCATOR, CANARY_ALIAS_VAR)
    monkeypatch.setenv(CANARY_ALIAS_VAR, CANARY_ALIAS_NAME)
    quality.sanitize_process_env()

    code = (
        "from offline_policy import socket_guard_installed\n"
        "import os\n"
        "print('PRESENT', 'POLZA_API_KEY' in os.environ)\n"
        f"print('ALIAS', '{CANARY_ALIAS_VAR}' in os.environ)\n"
        "print('GUARD', socket_guard_installed())\n"
    )
    fake = quality.Check("probe", [sys.executable, "-c", code])
    result = quality.run_check(fake, cwd=REPO_ROOT)
    assert result.exit_code == 0, result.stderr
    assert "PRESENT False" in result.stdout
    assert "ALIAS False" in result.stdout
    assert "GUARD True" in result.stdout
    assert CANARY_DEFAULT_NAME not in result.stdout
    assert CANARY_ALIAS_NAME not in result.stderr
