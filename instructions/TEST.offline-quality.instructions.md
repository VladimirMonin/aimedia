---
applyTo: "tests/**, scripts/quality.py, pyproject.toml, .github/workflows/**, uv.lock"
name: "TEST.OfflineQuality"
description: "Читай при изменении tests/, scripts/quality.py, конфигурации pytest/Ruff/mypy или CI: обязательные offline-гейты, режимы quality, сетевая изоляция и изоляция секретов."
---

# TEST — Offline-гейты и качество aimedia

Эта инструкция фиксирует устойчивый контур проверок проекта. Она описывает
**реализованное** на E01 и не выдаёт будущие suites за существующие.

## Режимы контура проверок

Обязательная точка входа — `scripts/quality.py`:

```bash
uv run --locked --no-env-file python scripts/quality.py quick
uv run --locked --no-env-file python scripts/quality.py full --report-dir artifacts/quality
uv run --locked --no-env-file python scripts/quality.py release --report-dir artifacts/release
```

- `quick` — Ruff lint + format check, mypy, обязательные suites.
- `full` — то же плюс coverage по `aimedia` (branch measurement); `--report-dir`
  сохраняет `summary.json`, `stdout.txt`, `stderr.txt`.
- `release` — `full` плюс сборка distributions (`uv build`).

JUnit/coverage XML и live-evidence добавляются вместе с соответствующими suites
на следующих этапах; сейчас их в harness нет.

Правила harness, которые нельзя ослаблять:

- команды запускаются списком аргументов через `subprocess` без `shell=True`;
- **сырой** exit code каждой обязательной проверки сохраняется; ненулевой код
  дочернего процесса становится итоговым кодом, а не маскируется последующим
  успехом;
- отсутствие обязательного набора тестов — failure (код `66`), а не зелёный итог:
  каталог проверяется до запуска, а **каждый** обязательный suite дополнительно
  проходит `pytest --collect-only`; ноль собранных тестов в отдельном suite —
  failure `66`, даже если общий pytest-запуск всех suites зелёный за счёт соседних
  каталогов;
- `SKIPPED`/`XFAILED`/`NOT_COLLECTED` не считаются `PASSED`;
- секретные переменные удаляются из окружения harness до spawn дочерних проверок,
  а окружение каждого ребёнка строится через общий `child_process_env()`.

Список `REQUIRED_SUITES` в `scripts/quality.py` расширяется вместе с появлением
реальных каталогов тестов; добавлять несуществующий suite заранее запрещено.
Сейчас обязательны `tests/unit`, `tests/architecture`, `tests/cli`, `tests/tooling`,
`tests/security`, `tests/contracts`, `tests/integration`.

Каталог `tests/support/` содержит тестовые doubles (fake provider) — это **не** suite
и не входит в `REQUIRED_SUITES`; pytest его не собирает. Он становится импортируемым
только потому, что `tests/conftest.py` добавляет его в `sys.path`, а production-код
не имеет права его импортировать (проверяет `tests/architecture/test_dependencies.py`).

## Offline-изоляция

Единый владелец правил — `tests/offline/offline_policy.py`; его же импортирует
`scripts/quality.py`. Это единственное место, где перечислены имя guard, имена
секретов и правило `PYTHONPATH`.

- Тестовый контур запрещает подключения к любому не-loopback адресу. Guard
  реализован в `tests/conftest.py` на уровне `socket` (`connect`, `connect_ex`,
  `getaddrinfo`) и не является pytest-маркером: он ставится до collection из
  `offline_policy.ensure_socket_guard()` и не снимается отдельным тестом.
  Loopback разрешён и покрыт реальным тестом с локальным HTTP-эмулятором.
- Политика распространяется на **дочерние Python-процессы**, а не только на
  pytest-процесс. `offline_policy.child_process_env()` возвращает окружение
  ребёнка: секреты удалены, а `tests/offline` добавлен в `PYTHONPATH`, поэтому
  интерпретатор импортирует `tests/offline/sitecustomize.py` и устанавливает тот
  же guard. `scripts/quality.py` строит так окружение каждой обязательной проверки.
- Секретные переменные удаляются трижды: при импорте `tests/conftest.py` (до
  collection), в autouse-fixture каждого теста и в окружении каждого ребёнка.
  Кроме `POLZA_API_KEY` очищается имя-алиас, на которое ссылается
  `AIMEDIA_POLZA_API_KEY_ENV`. Значения никогда не читаются и не логируются;
  возвращаются только имена удалённых переменных.
- Тестовые данные направляются в временные каталоги; пользовательский data-root не
  создаётся и не изменяется (проверяется `tests/cli/test_bootstrap.py`).
- `uv run --locked --no-env-file` используется во всех проверках: `--locked`
  запрещает молчаливое изменение lockfile, `--no-env-file` отключает загрузку
  `.env`. `--no-env-file` не удаляет уже экспортированные переменные — поэтому
  тестовая изоляция дополнительно очищает окружение.

**Честная граница изоляции.** Покрыты Python-процессы, запущенные тем же
интерпретатором и унаследовавшие `PYTHONPATH`. Произвольный внешний бинарник
(`curl`, `git`, браузер) этим guard не перехватывается — для него нужна отдельная
изоляция уровня ОС, и он не считается покрытым.

## Обязательные команды

```bash
uv sync --locked
uv run --locked --no-env-file aimedia --help
uv run --locked --no-env-file aimedia version --json
uv run --locked --no-env-file ruff check .
uv run --locked --no-env-file ruff format --check .
uv run --locked --no-env-file mypy src/aimedia
```

## Конфигурация

- `requires-python = ">=3.12,<3.13"`; базовый интерпретатор — Python 3.12,
  зафиксированный в `.python-version`.
- Build backend — `hatchling` (единственная реализация).
- Ruff: `extend-exclude = ["docs"]` — Markdown плана содержит Python-примеры и не
  является кодом проекта.
- mypy: `strict = true`, `files = ["src/aimedia"]`.
- pytest: `--strict-markers`; неизвестный маркер — ошибка конфигурации.
- pytest: `pythonpath = ["tests/offline"]`, поэтому conftest и тесты видят общую
  политику и без ручной правки `sys.path`; сам conftest всё равно добавляет
  каталог, чтобы работать при прямом запуске.
- CI (`.github/workflows/ci.yml`) повторяет `full` на Windows и Linux offline, без
  секретов и без live-вызовов.

## Границы

- Live-проверки Polza (`tests/live`) не входят в обычные режимы и запускаются
  только по явному разрешению с лимитом. На E01 каталога `tests/live` ещё нет.
- CI и quality-контур не публикуют и не устанавливают пакет в пользовательский
  tool root; изолированная установка wheel — отдельный шаг приёмки.
