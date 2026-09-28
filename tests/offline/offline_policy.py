"""Процессно-независимая offline-политика тестов aimedia.

Модуль — единственное место, где описаны:

1. запрет подключений к внешней сети на уровне `socket` (loopback разрешён для
   provider-emulator);
2. имена секретных переменных окружения и их удаление из окружения;
3. окружение дочернего процесса: санитизированное и с каталогом этого модуля в
   `PYTHONPATH`.

Модуль используют `tests/conftest.py` (до collection и в fixtures),
`scripts/quality.py` (до spawn дочерних проверок) и `sitecustomize.py` этого же
каталога. Последний интерпретатор импортирует автоматически при старте любого
Python-процесса, у которого этот каталог есть в `PYTHONPATH`.

Честная граница: политика покрывает Python-процессы, запущенные тем же
интерпретатором и унаследовавшие `PYTHONPATH`. Произвольный внешний бинарник
(`curl`, `git`, браузер) она не перехватывает — для него нужна отдельная изоляция
уровня ОС.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Mapping, MutableMapping
from typing import Any

# Каталог этого модуля. Он же добавляется в PYTHONPATH дочерних процессов, чтобы
# `sitecustomize.py` установил guard в ребёнке.
GUARD_DIR = os.path.dirname(os.path.abspath(__file__))

# Переменные окружения, значения которых считаются секретами и не должны попадать
# ни в тестовый процесс, ни в его потомков.
SECRET_ENV_VARS: tuple[str, ...] = ("POLZA_API_KEY",)

# Локатор имени секрета: значение этой переменной — имя другой переменной, в
# которой лежит ключ. Само значение локатора секретом не является.
API_KEY_ENV_LOCATOR = "AIMEDIA_POLZA_API_KEY_ENV"

PYTHONPATH_VAR = "PYTHONPATH"

# Адреса, к которым тестам разрешено подключаться: локальный эмулятор provider.
# Всё остальное запрещено.
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_GUARD_MARKER = "_aimedia_offline_guard"


class ExternalNetworkBlocked(RuntimeError):
    """Попытка неожиданного внешнего сетевого подключения."""


def secret_env_names(environ: Mapping[str, Any]) -> tuple[str, ...]:
    """Имена переменных окружения, значения которых считаются секретами.

    Кроме имени по умолчанию учитывается имя, на которое ссылается
    `AIMEDIA_POLZA_API_KEY_ENV`: при экспортированном ключе-алиасе оно тоже
    секретно.
    """
    names = list(SECRET_ENV_VARS)
    aliased = environ.get(API_KEY_ENV_LOCATOR)
    if isinstance(aliased, str) and aliased.strip() and aliased.strip() not in names:
        names.append(aliased.strip())
    return tuple(names)


def scrub_secret_env(environ: MutableMapping[str, Any]) -> tuple[str, ...]:
    """Удалить секретные переменные из окружения, не читая их значения.

    Возвращает только имена удалённых переменных, поэтому результат можно
    логировать, не раскрывая секрет.
    """
    removed: list[str] = []
    for name in secret_env_names(environ):
        if name in environ:
            del environ[name]
            removed.append(name)
    return tuple(removed)


def _guard_pythonpath(env: Mapping[str, Any]) -> str:
    existing = env.get(PYTHONPATH_VAR)
    parts = [part for part in str(existing).split(os.pathsep) if part] if existing else []
    if GUARD_DIR not in parts:
        parts.insert(0, GUARD_DIR)
    return os.pathsep.join(parts)


def child_process_env(environ: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Окружение дочернего процесса тестов.

    Секреты удалены, а `PYTHONPATH` дополнен каталогом guard, поэтому
    `sitecustomize.py` установит сетевой guard в каждом Python-ребёнке.
    """
    env = dict(os.environ if environ is None else environ)
    scrub_secret_env(env)
    env[PYTHONPATH_VAR] = _guard_pythonpath(env)
    return env


def _is_allowed_address(address: object) -> bool:
    """Разрешён ли адрес: только unix-socket и loopback."""
    if isinstance(address, bytes):  # AF_UNIX
        return True
    if isinstance(address, (list, tuple)) and address:
        host = address[0]
    elif isinstance(address, str):
        host = address
    else:
        return False

    if not isinstance(host, str):
        return False
    if host in ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback


def socket_guard_installed() -> bool:
    """Установлен ли сетевой guard в текущем процессе."""
    return bool(getattr(socket.socket.connect, _GUARD_MARKER, False))


def ensure_socket_guard() -> bool:
    """Установить сетевой guard один раз на процесс.

    Возвращает `True`, если guard установлен этим вызовом, и `False`, если он уже
    действовал. Отдельный тест не может снять guard: заменённые функции помечены
    и повторно не оборачиваются.
    """
    if socket_guard_installed():
        return False

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> object:
        if not _is_allowed_address(address):
            raise ExternalNetworkBlocked(
                f"Внешнее сетевое подключение запрещено в offline-тестах: {address!r}"
            )
        return real_connect(self, address)  # type: ignore[arg-type]

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if not _is_allowed_address(address):
            raise ExternalNetworkBlocked(
                f"Внешнее сетевое подключение запрещено в offline-тестах: {address!r}"
            )
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> list[tuple[object, ...]]:
        if isinstance(host, bytes):
            host = host.decode()
        if host not in ALLOWED_HOSTS:
            raise ExternalNetworkBlocked(f"DNS-резолвинг внешнего хоста запрещён: {host!r}")
        return real_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    setattr(guarded_connect, _GUARD_MARKER, True)
    setattr(guarded_connect_ex, _GUARD_MARKER, True)
    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    return True
