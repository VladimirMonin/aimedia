"""Общая конфигурация тестов aimedia.

Два независимых правила применяются к каждому тесту:

1. **Сетевая изоляция.** Guard на уровне `socket` запрещает подключения к
   любому не-loopback адресу. Это не pytest-маркер: он не может быть снят
   отдельным тестом «на всякий случай». Guard ставится до collection и
   распространяется на Python-потомков через каталог `tests/offline`.
2. **Изоляция секретов.** Реальные значения ключей удаляются из окружения
   тестового процесса до collection, а затем из окружения каждого теста, чтобы
   ни импорт тестового модуля, ни сам тест не зависели от экспортированного
   ключа разработчика (включая имя-алиас из `AIMEDIA_POLZA_API_KEY_ENV`).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Каталог процессно-независимой политики добавляется до импорта: conftest должен
# работать и когда pytest запущен без ini-опции `pythonpath`.
_OFFLINE_DIR = Path(__file__).resolve().parent / "offline"
if str(_OFFLINE_DIR) not in sys.path:
    sys.path.insert(0, str(_OFFLINE_DIR))

# `tests/support` содержит тестовые doubles (fake provider). Это не production код
# и не suite: каталог не собирается pytest и не входит в REQUIRED_SUITES.
_SUPPORT_DIR = Path(__file__).resolve().parent / "support"
if str(_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(_SUPPORT_DIR))

from offline_policy import (  # noqa: E402
    ensure_socket_guard,
    scrub_secret_env,
    secret_env_names,
)

# Секреты удаляются до collection.
scrub_secret_env(os.environ)
ensure_socket_guard()


@pytest.fixture(scope="session", autouse=True)
def _block_external_network() -> Iterator[None]:
    """Гарантировать сетевую изоляцию на всё время сессии.

    Guard уже установлен на импорте `conftest`; fixture подтверждает инвариант:
    отдельный тест не может оставить процесс без защиты.
    """
    assert ensure_socket_guard() is False, "offline-guard должен действовать до тестов"
    yield


@pytest.fixture(autouse=True)
def _scrub_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Удалить секретные переменные окружения из тестового процесса.

    Имена берутся из актуального окружения, поэтому алиас, заданный через
    `AIMEDIA_POLZA_API_KEY_ENV`, тоже очищается.
    """
    for name in secret_env_names(os.environ):
        monkeypatch.delenv(name, raising=False)
