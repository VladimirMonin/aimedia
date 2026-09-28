"""Автоматический offline-guard для дочерних Python-процессов тестов.

Интерпретатор импортирует `sitecustomize` при старте, если каталог `tests/offline`
есть в `PYTHONPATH`. Каталог добавляет `offline_policy.child_process_env()`, поэтому
любой Python-процесс, запущенный тестами, получает тот же сетевой guard, что и
сам pytest.

Ошибки импорта здесь не подавляются: молчаливый `sitecustomize` скрыл бы отказ
политики.
"""

from offline_policy import ensure_socket_guard

ensure_socket_guard()
