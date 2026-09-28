"""Сетевая изоляция тестового контура, включая дочерние Python-процессы.

In-process guard проверяется напрямую, а распространение политики на потомков —
реальным subprocess-запуском: ребёнок получает окружение через
`offline_policy.child_process_env()` и той же политикой обязан отказать во внешнем
подключении. Loopback разрешён, поэтому отдельный тест поднимает реальный
локальный HTTP-сервер и доказывает, что эмулятор provider не сломан.

Честная граница: покрыты Python-процессы, запущенные этим же интерпретатором и
унаследовавшие `PYTHONPATH`. Произвольный внешний бинарник (`curl`, `git`) guard не
перехватывает — для него нужна изоляция уровня ОС.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from offline_policy import child_process_env

TIMEOUT_SECONDS = 60

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_python(code: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        env=child_process_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=TIMEOUT_SECONDS,
    )


def test_external_connect_is_blocked() -> None:
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


def test_child_process_installs_socket_guard(tmp_path: Path) -> None:
    """Python-ребёнок стартует уже с активным guard, не только pytest-процесс."""
    code = (
        "from offline_policy import socket_guard_installed\n"
        "print('GUARD', socket_guard_installed())\n"
    )
    result = _run_python(code, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "GUARD True" in result.stdout


def test_child_process_external_socket_connect_fails(tmp_path: Path) -> None:
    """Дочерний тест не может подключиться к внешнему адресу."""
    code = (
        "import socket\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "sock.connect(('93.184.216.34', 80))\n"
        "print('CONNECTED')\n"
    )
    result = _run_python(code, cwd=tmp_path)
    assert result.returncode != 0
    assert "ExternalNetworkBlocked" in result.stderr
    assert "CONNECTED" not in result.stdout


def test_child_process_external_dns_fails(tmp_path: Path) -> None:
    code = "import socket\nsocket.getaddrinfo('example.com', 443)\nprint('RESOLVED')\n"
    result = _run_python(code, cwd=tmp_path)
    assert result.returncode != 0
    assert "ExternalNetworkBlocked" in result.stderr
    assert "RESOLVED" not in result.stdout


def test_child_process_real_http_client_fails(tmp_path: Path) -> None:
    """Реальный HTTP-клиент из stdlib в ребёнке не выходит во внешнюю сеть."""
    code = (
        "import urllib.request\n"
        "urllib.request.urlopen('http://example.com/', timeout=10)\n"
        "print('FETCHED')\n"
    )
    result = _run_python(code, cwd=tmp_path)
    assert result.returncode != 0
    assert "FETCHED" not in result.stdout
    assert "ExternalNetworkBlocked" in result.stderr


class _LoopbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - имя задано базовым классом
        body = b"loopback-emulator-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - сигнатура базового класса
        return


def test_child_process_can_reach_loopback_emulator(tmp_path: Path) -> None:
    """Guard не ломает loopback: ребёнок читает локальный HTTP-эмулятор."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackHandler)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code = (
            "import urllib.request\n"
            "from offline_policy import socket_guard_installed\n"
            f"body = urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=10).read()\n"
            "print('GUARD', socket_guard_installed())\n"
            "print('BODY', body.decode())\n"
        )
        result = _run_python(code, cwd=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=TIMEOUT_SECONDS)

    assert result.returncode == 0, result.stderr
    assert "GUARD True" in result.stdout
    assert "BODY loopback-emulator-ok" in result.stdout


def test_child_process_env_does_not_mutate_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`child_process_env` возвращает копию и не меняет окружение родителя."""
    import os

    monkeypatch.setenv("POLZA_API_KEY", "sk-canary-parent-scope")
    before = dict(os.environ)
    env = child_process_env()
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(
        Path(__file__).resolve().parent.parent / "offline"
    )
    assert "POLZA_API_KEY" not in env
    assert dict(os.environ) == before
