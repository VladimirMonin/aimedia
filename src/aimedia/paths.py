"""Пути данных, конфигурации и логов aimedia.

Каталоги вычисляются через `platformdirs` и не создаются при чтении. Тесты
подменяют системные переменные окружения, поэтому пользовательский data-root
не используется offline-проверками.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APPLICATION_NAME = "aimedia"
SETTINGS_FILE_NAME = "settings.toml"
LOG_FILE_NAME = "aimedia.log"


def default_data_dir() -> Path:
    """Вернуть каталог данных по умолчанию, не создавая его."""
    return Path(user_data_dir(APPLICATION_NAME))


def default_config_dir() -> Path:
    """Вернуть каталог пользовательской конфигурации, не создавая его."""
    return Path(user_config_dir(APPLICATION_NAME))


def user_settings_file() -> Path:
    """Путь к пользовательскому `settings.toml` (файл может не существовать)."""
    return default_config_dir() / SETTINGS_FILE_NAME


def logs_dir(data_dir: Path) -> Path:
    """Каталог диагностических логов внутри data-root."""
    return data_dir / "logs"


def log_file(data_dir: Path) -> Path:
    """Путь диагностического лог-файла внутри data-root."""
    return logs_dir(data_dir) / LOG_FILE_NAME
