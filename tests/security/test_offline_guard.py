"""Проверки сетевой изоляции тестового контура."""

from __future__ import annotations

import socket

import pytest


def test_external_connect_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Неожиданное внешнее подключение завершает тест ошибкой.

    Loopback разрешён, поэтому настоящий connect до 127.0.0.1:1 не падает по
    нашему guard, а даёт обычную сетевую ошибку. Внешний адрес должен быть
    заблокирован до попытки соединения.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="запрещено"):
            sock.connect(("93.184.216.34", 80))
    finally:
        sock.close()


def test_external_connect_ex_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="запрещено"):
            sock.connect_ex(("93.184.216.34", 80))
    finally:
        sock.close()


def test_external_dns_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="запрещён"):
        socket.getaddrinfo("example.com", 443)


def test_loopback_dns_allowed() -> None:
    infos = socket.getaddrinfo("127.0.0.1", 80)
    assert infos
