"""Общая конфигурация тестов aimedia.

Два независимых правила применяются к каждому тесту:

1. **Сетевая изоляция.** Guard на уровне `socket` запрещает подключения к
   любому не-loopback адресу. Это не pytest-маркер: он не может быть снят
   отдельным тестом «на всякий случай».
2. **Изоляция секретов.** Реальные значения ключей удаляются из окружения
   тестового процесса, чтобы offline-тесты не зависели от экспортированного
   ключа разработчика.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator

import pytest

# Переменные окружения, значения которых считаются секретами и не должны
# протекать в offline-тесты.
SECRET_ENV_VARS: tuple[str, ...] = ("POLZA_API_KEY",)

# Адреса, к которым тестам разрешено подключаться: локальный эмулятор provider
# в будущих этапах. Всё остальное запрещено.
_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ExternalNetworkBlocked(RuntimeError):
    """Попытка неожиданного внешнего сетевого подключения."""


def _is_allowed_address(address: object) -> bool:
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
    if host in _ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback


@pytest.fixture(scope="session", autouse=True)
def _block_external_network() -> Iterator[None]:
    """Запретить подключения к внешней сети на всё время сессии."""
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
        if host not in _ALLOWED_HOSTS:
            raise ExternalNetworkBlocked(f"DNS-резолвинг внешнего хоста запрещён: {host!r}")
        return real_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _scrub_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Удалить секретные переменные окружения из тестового процесса."""
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
