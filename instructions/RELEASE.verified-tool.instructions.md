---
applyTo: "README.md,docs/plans/release-board.md,docs/plans/progress/postrelease-*.md,docs/plans/progress/e11-*.md,docs/plans/progress/v*-refresh-*.md"
name: "RELEASE.VerifiedTool"
description: "Читай при установке, повторной проверке или явно разрешённом docs-only refresh опубликованного aimedia: Git-tag, release assets/provenance, scoped reinstall обычного uv tool, offline/live evidence, бюджет и изоляция пользовательских данных."
---

# RELEASE — Проверенная установка и эксплуатационная проверка uv tool

Владелец — установка, проверка и подготовка docs-only refresh **уже опубликованного**
image-only инструмента, не новый runtime. Исходные контракты: [план E10/E11](../docs/plans/README.md),
[CLI](CLI.public-image.instructions.md), [offline quality](TEST.offline-quality.instructions.md),
[Polza](PROVIDER.polza-media.instructions.md). Разрешение на проверку не разрешает
двигать тег, активировать модели или менять provider/security/recovery контракты.
Staging/commit/push принадлежат основному интегратору по [Git-инструкции](DOCS.commit_messages.instructions.md).

## 1. Зафиксировать источник и публичные assets

Проверь branch/worktree/status, чужие изменения и source SHA **до записей**.
Для принятого релиза используй опубликованный annotated tag, не `main`.
По умолчанию тег immutable; разрешение на установку/проверку не разрешает retag.
Текущий `source_commit` бери из публичного `release-manifest.json` и сопоставь
с peeled commit тега:

```bash
git ls-remote https://github.com/VladimirMonin/aimedia.git 'refs/tags/v0.1.0' 'refs/tags/v0.1.0^{}'
```

Исходная публикация `v0.1.0`: tag object `77079872c76c7bf198339c31de9c47208c58d742`,
peeled commit `f7fb04e6768aaf1c04c575166e5b73a5e4e95473` — историческая provenance,
не постоянное значение текущего тега. Из [assets релиза](https://github.com/VladimirMonin/aimedia/releases/tag/v0.1.0)
скачай manifest, `SHA256SUMS` и `runtime-constraints.txt` в новый owned каталог.
Сопоставь SHA-256 **фактических байтов** constraints со строкой в SHA256SUMS.
При использовании wheel/sdist проверь и их строки. Checksums из того же release
не являются независимой цифровой подписью. Несовпадение — STOP, не переиздание тега.

### Явно разрешённое исключение: docs-only refresh того же тега

Только отдельное решение владельца разрешает перенос существующего тега и замену
assets без смены версии. Именованное решение/дата/объём принадлежат отчёту refresh,
не общему правилу immutable. Интегратор выполняет Git/publication/tool install.
До замены сохрани прежние публичные assets и old tag object/peeled commit; после
commit зафиксируй new tag object/peeled commit и installed VCS commit. Старые
приёмки/evidence не переписывай и не выдавай за прогоны обновлённого payload.

Для docs-only кандидата достаточно одного diff, подтверждающего неизменность
runtime/tests/lock/pyproject, focused docs links/YAML/whitespace checks и одной
offline сборки wheel/sdist из existing cache без sync/установки зависимостей:

```bash
uv --no-config build --offline --out-dir <owned-assets-dir>
```

Перед spawn удали секреты по именам, включая alias `AIMEDIA_POLZA_API_KEY_ENV`;
не загружай `.env`. Проверь archive members: актуальные docs в sdist, навыки отдельно
в source/sdist, не wheel resources; без `.env`, БД, `.pi` и private artifacts.
Публичный корневой `.env.example` допустим: Git-ignore исключение только
`!/.env.example`, не глобальное `!.env.example`, чтобы templates внутри `.pi/`
и корневого `artifacts/` не попадали в sdist; `src/aimedia/artifacts/` — штатный код.
При недоступном offline backend остановись,
не устанавливай его молча. Full/OS/live/CI-watch повтор для такого refresh не нужен;
исторические 1667 / 1663+4 SKIPPED / coverage93 остаются evidence исходного runtime.

После independent review коммить те же bytes. В metadata запиши actual source
commit, old/new provenance и границы новых checks; manifest должен соответствовать
новым wheel/sdist/constraints/verification. Генерируй `SHA256SUMS` после финального
stamp metadata. Templates не upload-ready и не публикуются. Повторная установка
того же version/tag требует `--reinstall-package aimedia` (scoped refresh), не
`--upgrade` всех tools/глобального cache cleanup. Кратко сверяй установленный VCS
commit с новым peeled commit; version banner недостаточен. Никаких paid calls или
записей в пользовательскую историю ради docs-only refresh.

## 2. Обычный пользовательский uv tool, а не package-test окружение

Новая оболочка, без project `.venv`, `PYTHONPATH=src`, `UV_TOOL_DIR` и
`UV_TOOL_BIN_DIR`; не читай пользовательский TOML ради проверки. `--no-config`
отключает uv config discovery, но не очищает уже экспортированные secrets/env.
Используй **проверенный** asset; путь ниже относительно каталога скачивания:

```bash
uv --no-config tool install --python 3.12 --constraints runtime-constraints.txt git+https://github.com/VladimirMonin/aimedia.git@v0.1.0
uv --no-config tool list --show-paths
```

Это идемпотентный повтор для уже установленного того же релиза. `already installed`
или refresh entrypoint с неизменным окружением — допустимый успех, **не доказательство
чистой переустановки**. Для явно разрешённого same-tag refresh используй:

```bash
uv --no-config tool install --reinstall-package aimedia --python 3.12 --constraints runtime-constraints.txt git+https://github.com/VladimirMonin/aimedia.git@v0.1.0
```

Не добавляй `--upgrade`, editable, `main` или глобальный force.
Зафиксируй прежний/новый список tools; не обновляй соседние пользовательские tools.
Отдельно проверь PATH: `Get-Command aimedia | Select-Object Source` (PowerShell) либо
`command -v aimedia` (POSIX); сравни с entrypoint из tool receipt.

По пути из `tool list` используй Python именно tool environment (`Scripts/python.exe`
или `bin/python`), вне checkout и без source injection. Через `importlib.metadata`
проверь distribution version, `direct_url.json` (`vcs_info.requested_revision`,
`commit_id`, `vcs`) и отсутствие editable `dir_info`; receipt сам по себе недостаточен.
При полной эксплуатационной проверке сверяй package resources/dependencies с
источником/constraints, учитывая platform markers и CRLF/LF. Исходная приёмка
v0.1.0 установила 81 package-файл и 25 Windows pins (colorama Windows-only),
не нормативный счётчик любой будущей сборки. Для docs-only refresh достаточно
scoped provenance выше. Version banner — не provenance.

## 3. Полный offline gate прежде новых paid данных

Используй clean checkout принятого source SHA на Windows **и настоящем Linux/WSL**.
Если HEAD отличается, докажи равенство runtime/tests/lockfile с тегом, назови отдельно
docs/CI-only изменения и EOL-различия. Existing cache допустим после проверки SHA,
status и bytes; новый disposable checkout — только в owned Temp/cache, не среди
соседних пользовательских проектов. Не создавай новые envs без необходимости.

Из окружения удали секреты **по именам**, включая alias из
`AIMEDIA_POLZA_API_KEY_ENV`; не печатай значения. Тесты используют socket/child guard
и disposable config/data по [TEST](TEST.offline-quality.instructions.md).

```bash
uv --no-config sync --locked --offline
UV_OFFLINE=1 uv --no-config run --locked --offline --no-env-file python scripts/quality.py release --report-dir artifacts/quality/release-check
```

В PowerShell перед командами задай `$env:UV_OFFLINE = "1"`. Один `release` включает
lint/format/mypy, сбор каждого обязательного suite, полный pytest с branch coverage
и build; не требуй шести повторов quick/full/release. При CI-like captured stdout
`_TYPER_FORCE_DISABLE_TERMINAL=1` допустим по действующему CI/TEST-контракту, с явным
указанием в evidence, без удаления ANSI из результата или ослабления assertions.
Сохрани argv/cwd/raw exit каждого check и summary/stdout/stderr. SKIPPED ≠ PASSED.
`tests/live` и плановые `scripts/verify_tool_install.py`/`smoke_installed.py` не считай
существующей реализацией: сверяй дерево, не запускай отсутствующие scripts.

## 4. Installed local smoke и отдельно разрешённый live

Запускай **обычный установленный executable** новым процессом вне checkout,
`subprocess` списком argv без `shell=True`. Для local smoke — без ключа, с
`tests/offline/sitecustomize.py` как единственным guard PYTHONPATH (не `src`).
Явные owned `--config` и `--data-dir` обязательны; `config init --file` указывает
ровно новый owned TOML. Проверь возвращённый путь, local version/help (human/JSON/raw),
все packaged topics, models/providers/config/history и invalid-input exit envelopes.
Не допускай платных тестов в offline harness.

Источник истины — explicit TOML, SQLite и managed bytes, не gallery/cache. До запуска
проверь ownership fresh roots, отсутствие redirects/коллизий и существующий `--out`.
Первая запись `config init` — создание собственного parent и exclusive TOML;
первое открытие history может создать БД/применить migrations. Не направляй эти
операции в default user roots; существующие данные защищены backup-контрактом ниже.

Live возможен только после **явного разрешения и лимита**. Перед первым spawn:

- сверить fresh официальные Polza model IDs, режимы/референсы и выбранные upstream
  тарифы; YAML и цена «от» не доказывают доступность/верхнюю цену;
- заморозить case plan, максимум paid попыток, Decimal ledger и reservations;
  `old known + old UNKNOWN + new known + new unknown + inflight caps + next cap ≤ budget`;
  резервировать весь batch до spawn, не освобождать исторический UNKNOWN;
- выделить fresh no-clobber data-root/evidence; старые uncertain Jobs/БД не трогать;
  использовать собственные nonsensitive reference copies, не gallery originals;
- брать только необходимый credential field через отдельно проверенный stdlib reader,
  `python -I`, memory-only stdout → scoped child `POLZA_API_KEY`. Не исполнять shell
  descriptor; неоднозначность/изменённый reader — STOP. Ключ не идёт в argv, TOML,
  БД, logs, raw responses или evidence; проверять leakage в памяти до публикации;
- live child не наследует offline guard/source PYTHONPATH; никакой подмены transport,
  DNS/TLS/request/application, mocks или observer для доказательства plain CLI.

Actual RUB берётся из confirmed истории/CLI/readonly SQLite, **не каталожной оценки**.
Только known usable completed result позволяет заменить резерв фактической ценой.
Unknown/unusable/missing price/submit uncertainty или неожиданный provider refusal
останавливает новые paid calls, сохраняет резерв. Допустим bounded GET-only sync
только явно известного **нового** ref; не угадывай binding по времени. Hidden paid
retry/fallback и paid повтор ради исправления helper запрещены. Deliberate `jobs retry`
— отдельный заранее разрешённый новый Job/резерв, не recovery.

## 5. Reconciliation, evidence и завершение

После paid phase проверь restart show/recent/search по prompts/refs/artifacts,
статусы/costs, readonly SQLite integrity/FKs/migrations и Decimal суммы. Декодируй
изображения и сравни SHA/size/dimensions. Managed refs проверяй после удаления
**только новых owned исходных копий**, сохраняя archive originals; retry lineage и
старый Job должны остаться неизменны. Completed sync — guarded local no-op; отказ
не требует нового submit. Visual inspection — дополнительная scoped оценка.

Сохраняй локально DB/managed inputs/outputs и при необходимости удобную gallery с
манифестом копий. В tracked отчёт попадают только sanitized assertions, суммы,
source/install provenance, redacted IDs, границы и пути evidence; никаких реальных
ключей, prompts, images, БД или raw logs. Не приписывай точный wire POST count, если
наблюдались лишь CLI invocations/Jobs: same-runtime offline submit contracts — другое
доказательство. Сохрани и failed helper attempts, не только последнюю зелёную попытку.

После quiescence сохраняй hashes/diff manifest кандидата, затем independent review.
Cleanup касается только точных новых owned Temp dirs после сохранения evidence,
не user history/artifacts и не чужого existing cache. Для операции с существующим
пользовательским data-root или рискованного обновления/миграции сначала нужен
[проверенный quiescent backup+restore](../docs/plans/backup-contract.md).
Обычное обновление — отдельное разрешение и **новый** тег/peeled SHA/constraints/
verification, не перемещение старого тега. Исключение — явно разрешённый владельцем
docs-only refresh по разделу 1; совместимость БД/откат не предполагаются автоматически.
