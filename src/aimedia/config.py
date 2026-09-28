"""Разрешение пользовательской конфигурации aimedia.

Приоритет значений: явные CLI-параметры → пользовательский TOML → переменные
окружения `AIMEDIA_*` → значения по умолчанию. Установленный инструмент не читает
`.env` из текущего каталога: dotenv-источник отсутствует, а `.env` загружает только
явный development-запуск (`uv run --env-file`). Секрет не хранится в настройках —
хранится только имя переменной окружения, в которой он лежит.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from aimedia import paths

# Единственное имя переменной окружения, задающей альтернативный config-файл.
CONFIG_FILE_ENV_VAR = "AIMEDIA_CONFIG"
DEFAULT_POLZA_API_KEY_ENV = "POLZA_API_KEY"
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Переменные окружения, участвующие в разрешении настроек. Их значения не читаются
# для диагностики: `config_loaded` сообщает только имена источников.
SETTINGS_ENV_VARS: tuple[str, ...] = (
    "AIMEDIA_DATA_DIR",
    "AIMEDIA_LOG_LEVEL",
    "AIMEDIA_LIVE_ENABLED",
    "AIMEDIA_POLZA_API_KEY_ENV",
    CONFIG_FILE_ENV_VAR,
)


class Settings(BaseSettings):
    """Настройки приложения с явным источником каждого значения."""

    model_config = SettingsConfigDict(
        env_prefix="AIMEDIA_",
        env_file=None,
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
        # Ошибка валидации не должна печатать недопустимое значение: им может быть
        # секрет, ошибочно переданный в переменную настройки.
        hide_input_in_errors=True,
    )

    data_dir: Path = Field(default_factory=paths.default_data_dir)
    log_level: str = DEFAULT_LOG_LEVEL
    live_enabled: bool = False
    polza_api_key_env: str = DEFAULT_POLZA_API_KEY_ENV

    # Расположение config-файла — локатор источника, а не пользовательское значение.
    # Алиас даёт ровно одно имя переменной окружения (`AIMEDIA_CONFIG`) и не создаёт
    # второй недокументированной формы `AIMEDIA_CONFIG_FILE`.
    config_file: Path | None = Field(default=None, validation_alias=CONFIG_FILE_ENV_VAR)

    _sources: tuple[str, ...] = PrivateAttr(default=("default",))

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in LOG_LEVELS:
            # Только перечень допустимых уровней, без самого значения: пользователь
            # мог ошибочно поместить секрет в `AIMEDIA_LOG_LEVEL`/`--log-level`.
            raise ValueError(f"Недопустимый уровень логирования; ожидается {LOG_LEVELS}")
        return normalized

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Собрать источники в порядке убывания приоритета.

        Dotenv-источник намеренно отсутствует: `.env` загружает только явный
        development-запуск, а не сам установленный инструмент.
        """
        sources: list[PydanticBaseSettingsSource] = [init_settings]
        config_file = _requested_config_file(init_settings)
        if config_file is not None:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=config_file))
        sources.append(env_settings)
        return tuple(sources)

    @classmethod
    def load(
        cls,
        *,
        cli_overrides: Mapping[str, Any] | None = None,
        config_file: Path | None = None,
    ) -> Settings:
        """Построить настройки и запомнить участвовавшие имена источников."""
        overrides: dict[str, Any] = dict(cli_overrides or {})
        explicit_cli = bool(overrides) or config_file is not None
        selected = config_file if config_file is not None else _config_file_from_env()
        if selected is not None:
            overrides["config_file"] = selected
        settings = cls(**overrides)
        settings._sources = _active_sources(explicit_cli, selected)
        return settings

    @property
    def sources(self) -> tuple[str, ...]:
        """Имена источников в порядке приоритета, без каких-либо значений."""
        return self._sources

    def resolve_api_key(self) -> str | None:
        """Прочитать секрет по имени переменной окружения.

        Значение никогда не сохраняется в настройках, config-файле, SQLite или
        логах; метод возвращает его только вызывающему коду момента использования.
        """
        return os.environ.get(self.polza_api_key_env) or None


def _config_file_from_env() -> Path | None:
    raw = os.environ.get(CONFIG_FILE_ENV_VAR)
    return Path(raw) if raw else None


def _requested_config_file(init_settings: PydanticBaseSettingsSource) -> Path | None:
    """Найти config-файл, переданный в конструктор.

    Pydantic хранит init-аргументы под именем поля, поэтому значение приходит
    либо как `config_file`, либо под своим validation alias (`AIMEDIA_CONFIG`).
    """
    init_kwargs = getattr(init_settings, "init_kwargs", {})
    provided = init_kwargs.get("config_file") or init_kwargs.get(CONFIG_FILE_ENV_VAR)
    if provided is not None:
        return provided if isinstance(provided, Path) else Path(str(provided))
    return _config_file_from_env()


def _active_sources(explicit_cli: bool, config_file: Path | None) -> tuple[str, ...]:
    """Имена источников в порядке приоритета, без значений настроек.

    `cli` отражает явные CLI-переопределения, а не сам факт наличия выбранного
    config-файла: локатор `AIMEDIA_CONFIG` не становится CLI-источником.
    """
    names: list[str] = []
    if explicit_cli:
        names.append("cli")
    if config_file is not None and config_file.is_file():
        names.append("config")
    if any(name in os.environ for name in SETTINGS_ENV_VARS):
        names.append("env")
    names.append("default")
    return tuple(names)
