# aimedia

Личная Python 3.12 image-only CLI утилита через Polza: генерация/batch, SQLite
история, managed reference copies, retry/sync, FTS5 поиск, точные расходы и
автономная Markdown справка. Исходный runtime `f7fb04e6` принят: Windows/Linux offline,
wheel/sdist и установленный пакет, агентский walkthrough, реальные Qwen/GPT
сценарии. [Отчёт и границы приёмки](docs/plans/progress/e10-f7fb04e6.acceptance.md).
Опубликован [релиз v0.1.0](https://github.com/VladimirMonin/aimedia/releases/tag/v0.1.0):
исходная публикация и её CI зафиксированы в [отчёте E11](docs/plans/progress/e11-v0.1.0.publication.md).
Владелец явно разрешил docs-only обновление сборки с переносом того же тега и
заменой assets, без смены версии/runtime. [Решение и границы обновления](docs/plans/progress/v0.1.0-refresh-2026-10-02.md).
Текущий payload тега сверяйте с `source_commit` публичного `release-manifest.json`,
а не с историческим SHA приёмки.

## Установка релиза

Скачайте `runtime-constraints.txt` из assets релиза в текущий каталог:

```bash
uv --no-config tool install --python 3.12 --constraints runtime-constraints.txt git+https://github.com/VladimirMonin/aimedia.git@v0.1.0
aimedia --version
aimedia --help
```

В assets релиза доступны wheel/sdist, `runtime-constraints.txt`, `verification.json`,
`release-manifest.json` и `SHA256SUMS`. Сверьте constraints/checksums с текущими assets;
после установки `direct_url.json` должен соответствовать payload тега из manifest.
Исходная установка → `f7fb04e6` (81 package-файл, 25 pins) и Windows/Linux CI —
исторические результаты, не новая проверка docs-only сборки. Каталог моделей
остаётся experimental.

Если `v0.1.0` уже установлен и владелец явно разрешил обновление **того же тега**,
обычный install может оставить прежнюю копию. После публикации обновлённых assets
и скачивания их constraints переустановите только `aimedia` с refresh пакета:

```bash
uv --no-config tool install --reinstall-package aimedia --python 3.12 --constraints runtime-constraints.txt git+https://github.com/VladimirMonin/aimedia.git@v0.1.0
```

Соседние tools и общий cache не обновляйте/не очищайте.
[Проверенная установка и повторная проверка](instructions/RELEASE.verified-tool.instructions.md) —
checksum assets, tag/provenance, offline gates и отдельно разрешённый paid plan.
[Фактическая postrelease-проверка](docs/plans/progress/postrelease-v0.1.0-verification-2026-10-01.md):
три модели, batch/retry, actual27.8 RUB; прежний UNKNOWN77 RUB сохранён.

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

`--allow-experimental` явно разрешает experimental запись каталога: наличие
отдельного live receipt не активирует все модели/режимы как stable. Проверенные
комбинации перечислены в отчёте. `--out` требует существующий каталог;
по умолчанию файл в managed outputs.
Промпты inline/file перемежаются в исходном порядке; `--image` повторяется.
Ссылки на проверенные copies остаются читаемыми после удаления исходников.

```bash
uv run --locked --no-env-file aimedia image batch prompts/*.md --model qwen-image-2-1 --allow-experimental --concurrency 3 --json
```

**Один выходной image на Job/POST**; `--max-images > 1` отклоняется до HTTP.
Несколько выходных изображений отложены по CN-05, batch независимых Jobs работает.
GPT binding `gpt-5-4-image-2-mie` → canonical `openai/gpt-5.4-image-2` через MIE:
1K/2K/4K, ratios auto/1:1/9:16/16:9/4:3/3:4; auto только1K, square не4K.
Prompt≤5000, до16 PNG/JPEG/WebP refs; `--image` повторяется в нужном порядке.
Все15 setting pairs проверены offline, шесть representative pairs/ref0–3 — live.
Тарифы MIE4/7/11 RUB; image price filter11 — guard, не гарантия Job total/billing.
`quality`/`seed` для этого binding не поддерживаются. Используйте существующие
локальные `boat.png` и `cup.jpg` в следующем **платном** примере:

```text
uv run --locked --no-env-file aimedia image generate --prompt "A still life combining the boat and cup references" --model gpt-5-4-image-2-mie --allow-experimental --resolution 2K --aspect-ratio 1:1 --image boat.png --image cup.jpg --max-images 1 --format webp --keep-original --json
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

## Навыки для агента

[Каталог docs/skills/](docs/skills/README.md) содержит три корневых пакета и четыре
`SKILL.md`: `aimedia` — image CLI; `visual-story-director` — текстовая подготовка
истории, раскадровки и промптов; `infographic-designer` — master для создания,
аудита и точечного исправления инфографики, с вложенным `screenshot-explainer`
для пояснения реального UI. Ссылки на все входы и подключение — в каталоге.
Master раскрывает выбранный сценарий постепенно; один агент выполняет роли
последовательно. Навыки доступны отдельно в Git source/tag и sdist (`docs/skills/`),
но не в wheel/установленном uv tool v0.1.0 и не устанавливаются автоматически.
Их наличие не добавляет CLI рендерер или разрешение на платный API.

## Offline проверки

```bash
uv run --locked --offline --no-env-file python scripts/quality.py full --report-dir artifacts/quality
UV_OFFLINE=1 uv run --locked --offline --no-env-file python scripts/quality.py release --report-dir artifacts/release
```

В PowerShell задайте `$env:UV_OFFLINE = "1"` перед release-командой. Тесты используют
только disposable SQLite/FS/MockHTTP и guarded subprocess, не пользовательские данные.
План/границы/actual evidence — [docs/plans/README.md](docs/plans/README.md),
[release-board](docs/plans/release-board.md).
