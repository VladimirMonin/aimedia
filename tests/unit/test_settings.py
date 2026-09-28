"""Проверки приоритета настроек, изоляции data-root и явного dotenv.

Тесты доказывают фактическое поведение `aimedia.config.Settings`: установленный
инструмент не ищет `.env` в текущем каталоге, а значения приходят только из
явных CLI-переопределений, выбранного TOML, переменных `AIMEDIA_*` и defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aimedia import paths
from aimedia.config import (
    CONFIG_FILE_ENV_VAR,
    DEFAULT_LOG_LEVEL,
    Settings,
)

# Все переменные, участвующие в разрешении настроек: тест делает окружение
# детерминированным независимо от shell разработчика.
_SETTINGS_ENV_VARS: tuple[str, ...] = (
    "AIMEDIA_DATA_DIR",
    "AIMEDIA_LOG_LEVEL",
    "AIMEDIA_LIVE_ENABLED",
    "AIMEDIA_POLZA_API_KEY_ENV",
    CONFIG_FILE_ENV_VAR,
)


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать любые AIMEDIA_* переменные, чтобы тесты не зависели от окружения."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_config(tmp_path: Path, body: str) -> Path:
    config = tmp_path / "settings.toml"
    config.write_text(body, encoding="utf-8")
    return config


def test_defaults_use_platformdirs_data_dir() -> None:
    settings = Settings.load()
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.data_dir == paths.default_data_dir()
    assert settings.sources == ("default",)


def test_cli_override_wins_over_config_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, 'log_level = "warning"\ndata_dir = "from-toml"\n')
    monkeypatch.setenv("AIMEDIA_LOG_LEVEL", "debug")
    settings = Settings.load(cli_overrides={"log_level": "error"}, config_file=config)
    assert settings.log_level == "ERROR"
    assert settings.data_dir == Path("from-toml")
    assert settings.sources == ("cli", "config", "env", "default")


def test_config_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(tmp_path, 'log_level = "warning"\n')
    monkeypatch.setenv("AIMEDIA_LOG_LEVEL", "debug")
    settings = Settings.load(config_file=config)
    assert settings.log_level == "WARNING"
    assert settings.sources == ("cli", "config", "env", "default")


def test_explicit_config_file_is_applied(tmp_path: Path) -> None:
    """Явно выбранный TOML действительно задаёт значения, а не только имя источника."""
    config = _write_config(tmp_path, 'data_dir = "from-toml"\n')
    settings = Settings.load(config_file=config)
    assert settings.data_dir == Path("from-toml")
    assert settings.sources == ("cli", "config", "default")


def test_constructor_applies_config_file(tmp_path: Path) -> None:
    """Прямая конструирование с `config_file` использует тот же TOML-источник."""
    config = _write_config(tmp_path, 'log_level = "critical"\n')
    assert Settings(config_file=config).log_level == "CRITICAL"


def test_env_var_selects_config_file_without_cli_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AIMEDIA_CONFIG` выбирает файл, но сам локатор не становится CLI-источником."""
    config = _write_config(tmp_path, 'log_level = "warning"\n')
    monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(config))
    settings = Settings.load()
    assert settings.log_level == "WARNING"
    assert settings.sources == ("config", "env", "default")


def test_env_override_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIMEDIA_LOG_LEVEL", "debug")
    monkeypatch.setenv("AIMEDIA_LIVE_ENABLED", "1")
    settings = Settings.load()
    assert settings.log_level == "DEBUG"
    assert settings.live_enabled is True
    assert settings.sources == ("env", "default")


def test_data_dir_env_isolates_user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AIMEDIA_DATA_DIR` заменяет системный data-root, не создавая его."""
    isolated = tmp_path / "temp-data"
    monkeypatch.setenv("AIMEDIA_DATA_DIR", str(isolated))
    settings = Settings.load()
    assert settings.data_dir == isolated
    assert not isolated.exists()
    assert settings.data_dir != paths.default_data_dir()


def test_missing_explicit_config_file_falls_back_to_defaults(tmp_path: Path) -> None:
    settings = Settings.load(config_file=tmp_path / "absent.toml")
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert "config" not in settings.sources


def test_dotenv_is_not_read_implicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` в текущем каталоге не влияет на установленный инструмент."""
    (tmp_path / ".env").write_text(
        "AIMEDIA_LOG_LEVEL=CRITICAL\nAIMEDIA_POLZA_API_KEY_ENV=LEAKY_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = Settings.load()
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.polza_api_key_env == "POLZA_API_KEY"
    assert settings.sources == ("default",)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="bogus")


def test_invalid_log_level_error_hides_input_value() -> None:
    """Ошибка валидации не повторяет недопустимое значение при выводе.

    Им может быть секрет, ошибочно помещённый в `AIMEDIA_LOG_LEVEL`/`--log-level`.
    `hide_input_in_errors` убирает значение из `str`/`repr` — ровно тех
    поверхностей, которые печатает необработанное исключение или трассировка.
    Программный `errors()` сохраняет `input` для отладки, поэтому CLI собирает
    безопасное сообщение по `loc`, а не рендерит `errors()` целиком (см.
    `tests/cli/test_bootstrap.py::test_invalid_log_level_as_env_is_safe_config_error`).
    """
    canary = "sk-canary-e01-settings-0123456789"
    with pytest.raises(ValidationError) as exc_info:
        Settings(log_level=canary)
    error = exc_info.value
    assert canary not in str(error)
    assert canary not in repr(error)
    assert "Недопустимый уровень логирования" in str(error)


def test_log_level_is_normalized() -> None:
    assert Settings(log_level="  debug ").log_level == "DEBUG"


def test_resolve_api_key_reads_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Секрет не хранится в настройках, а читается по имени переменной."""
    monkeypatch.setenv("POLZA_API_KEY", "sk-canary-value")
    settings = Settings.load()
    assert "sk-canary-value" not in repr(settings)
    assert settings.resolve_api_key() == "sk-canary-value"


def test_resolve_api_key_absent_returns_none() -> None:
    settings = Settings.load()
    assert settings.resolve_api_key() is None


def test_sources_never_leak_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Имена источников не содержат значений настроек или секрета."""
    monkeypatch.setenv("POLZA_API_KEY", "sk-should-not-appear")
    monkeypatch.setenv("AIMEDIA_LOG_LEVEL", "debug")
    settings = Settings.load()
    assert all("sk-should-not-appear" not in name for name in settings.sources)
    assert "debug" not in settings.sources
