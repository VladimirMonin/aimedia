# aimedia

Личная Python 3.12 image-only CLI утилита через Polza: генерация/batch, SQLite
история, managed reference copies, retry/sync, FTS5 поиск, точные расходы и
автономная Markdown справка. E07–E09 реализуются одной связной поставкой;
кандидат ещё требует независимого review/frozen SHA, Linux и ограниченной live
приёмки. Это **не опубликованный релиз**; модели документированы, но live unverified.

## Установка для разработки

Требуются [uv](https://docs.astral.sh/uv/) и Python 3.12.

```bash
uv sync --locked
```

## Первый запуск

```bash
uv run --locked --no-env-file aimedia --help
uv run --locked --no-env-file aimedia models list --json
uv run --locked --no-env-file aimedia help getting-started --raw
uv run --locked --no-env-file aimedia config init
```

Настройте `POLZA_API_KEY` вне argv/TOML/истории, через вашу переменную окружения
или secret manager. Установленная команда не ищет `.env` в cwd. TOML содержит
только `polza_api_key_env = "POLZA_API_KEY"`; `config init` не перезаписывает файл.
Для изолированных данных используйте `--data-dir` или `AIMEDIA_DATA_DIR`.

Следующая команда **платная**, запускайте только намеренно:

```bash
uv run --locked --no-env-file aimedia image generate --prompt "A watercolor laboratory robot" --model qwen-image-2-1 --allow-experimental --format webp --name robot
```

`--allow-experimental` подтверждает выбор documented, но ещё не проверенной live
модели. `--out` требует существующий каталог; по умолчанию файл в managed outputs.
Промпты inline/file перемежаются в исходном порядке; `--image` повторяется.
Ссылки на проверенные copies остаются читаемыми после удаления исходников.

```bash
uv run --locked --no-env-file aimedia image batch prompts/*.md --model qwen-image-2-1 --allow-experimental --concurrency 3 --json
uv run --locked --no-env-file aimedia jobs recent --json
uv run --locked --no-env-file aimedia jobs show 1 --json
uv run --locked --no-env-file aimedia jobs search "laboratory robot" --json
uv run --locked --no-env-file aimedia jobs costs --month --json
uv run --locked --no-env-file aimedia jobs sync 1 --json
```

Retry (`jobs retry 1 --allow-experimental`) — новая платная попытка и новый Job.
Sync — только известное remote execution, никогда submit. Timeout/Ctrl+C не
remote cancel. Unknown POST outcome не повторяется автоматически. RUB/USD
раздельно, unknown не равен нулю; каталожный тариф не подменяет actual billing.

JSON mode: весь stdout — один document, даже при argv error; диагностика stderr.
Локальные version/help/models/history/costs/config работают без API key/сети.

## Offline проверки

```bash
uv run --locked --offline --no-env-file python scripts/quality.py full --report-dir artifacts/quality
UV_OFFLINE=1 uv run --locked --offline --no-env-file python scripts/quality.py release --report-dir artifacts/release
```

В PowerShell задайте `$env:UV_OFFLINE = "1"` перед release-командой. Тесты используют
только disposable SQLite/FS/MockHTTP и guarded subprocess, не пользовательские данные.
План/границы/actual evidence — [docs/plans/README.md](docs/plans/README.md),
[release-board](docs/plans/release-board.md).
