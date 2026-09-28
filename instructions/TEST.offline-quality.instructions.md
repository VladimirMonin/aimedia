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
- отсутствие обязательного набора тестов — failure (код `66`), а не зелёный итог;
- `SKIPPED`/`XFAILED`/`NOT_COLLECTED` не считаются `PASSED`.

Список `REQUIRED_SUITES` в `scripts/quality.py` расширяется вместе с появлением
реальных каталогов тестов; добавлять несуществующий suite заранее запрещено.

## Offline-изоляция

- Тестовый контур запрещает подключения к любому не-loopback адресу. Guard
  реализован в `tests/conftest.py` на уровне `socket` (`connect`, `connect_ex`,
  `getaddrinfo`) и не является pytest-маркером: он не снимается отдельным тестом.
  Loopback разрешён для будущего provider-emulator.
- Секретные переменные (`POLZA_API_KEY`) удаляются из окружения каждого теста.
  Offline-тесты не полагаются на экспортированный ключ разработчика.
- Тестовые данные направляются в временные каталоги; пользовательский data-root не
  создаётся и не изменяется (проверяется `tests/cli/test_bootstrap.py`).
- `uv run --locked --no-env-file` используется во всех проверках: `--locked`
  запрещает молчаливое изменение lockfile, `--no-env-file` отключает загрузку
  `.env`. `--no-env-file` не удаляет уже экспортированные переменные — поэтому
  тестовая изоляция дополнительно очищает окружение.

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
- CI (`.github/workflows/ci.yml`) повторяет `full` на Windows и Linux offline, без
  секретов и без live-вызовов.

## Границы

- Live-проверки Polza (`tests/live`) не входят в обычные режимы и запускаются
  только по явному разрешению с лимитом. На E01 каталога `tests/live` ещё нет.
- CI и quality-контур не публикуют и не устанавливают пакет в пользовательский
  tool root; изолированная установка wheel — отдельный шаг приёмки.
