---
applyTo: "README.md,docs/plans/release-board.md,docs/plans/progress/postrelease-*.md,docs/plans/progress/e11-*.md"
name: "RELEASE.VerifiedTool"
description: "Читай при установке или повторной проверке опубликованного aimedia через обычный uv tool: immutable Git-tag, release constraints/checksums, provenance установленного пакета, offline/live evidence, бюджет и изоляция пользовательских данных."
---

# RELEASE — Проверенная установка и эксплуатационная проверка uv tool

Владелец — workflow **уже опубликованного** image-only инструмента, не новый
runtime и не публикация релиза. Исходные контракты: [план E10/E11](../docs/plans/README.md),
[CLI](CLI.public-image.instructions.md), [offline quality](TEST.offline-quality.instructions.md),
[Polza](PROVIDER.polza-media.instructions.md). Разрешение на проверку не разрешает
двигать тег, активировать модели или менять provider/security/recovery контракты.
Staging/commit/push принадлежат основному интегратору по [Git-инструкции](DOCS.commit_messages.instructions.md).

## 1. Зафиксировать источник и публичные assets

Проверь branch/worktree/status, чужие изменения и source SHA **до записей**.
Отдельно сохрани raw hashes исходников/tests/lockfile; clean Git status не доказывает
одинаковые raw bytes разных ОС: CRLF/LF нужно явно различать и сверять с Git blobs.
Для принятого релиза используй опубликованный immutable annotated tag, не `main`:

```bash
git ls-remote https://github.com/VladimirMonin/aimedia.git 'refs/tags/v0.1.0' 'refs/tags/v0.1.0^{}'
```

Для `v0.1.0` tag object — `77079872c76c7bf198339c31de9c47208c58d742`, peeled commit —
`f7fb04e6768aaf1c04c575166e5b73a5e4e95473`. Из [assets этого релиза](https://github.com/VladimirMonin/aimedia/releases/tag/v0.1.0)
скачай `SHA256SUMS` и `runtime-constraints.txt` в новый owned каталог.
Сопоставь SHA-256 **фактических байтов** constraints со строкой в SHA256SUMS; для
v0.1.0 это `df015e9929c9390f6ba758af9ca26a231b3194d4aa45b23af10b4c008f185947`.
При использовании wheel/sdist проверь и их строки. Checksums из того же release
не являются независимой цифровой подписью. Несовпадение — STOP, не переиздание тега.

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
чистой переустановки**. Не добавляй `--upgrade`, editable, `main` или глобальный force.
Зафиксируй прежний/новый список tools; не обновляй соседние пользовательские tools.
Отдельно проверь PATH: `Get-Command aimedia | Select-Object Source` (PowerShell) либо
`command -v aimedia` (POSIX); сравни с entrypoint из tool receipt.

По пути из `tool list` используй Python именно tool environment (`Scripts/python.exe`
или `bin/python`), вне checkout и без source injection. Через `importlib.metadata`
проверь distribution version, `direct_url.json` (`vcs_info.requested_revision`,
`commit_id`, `vcs`) и отсутствие editable `dir_info`; receipt сам по себе недостаточен.
Сверь все package `.py`/YAML/Markdown с raw Git blobs тега и dependencies с constraints,
учитывая platform markers. Для v0.1.0 ожидаются 81 package-файл и 25 Windows runtime
pins (Windows-only colorama не требуется на Linux). Version banner — не provenance.

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
Обновление — отдельное разрешение, **новый** тег/peeled SHA/constraints/verification,
не перемещение старого тега; совместимость БД/откат не предполагаются автоматически.
