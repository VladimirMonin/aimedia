# Повторная проверка опубликованного aimedia v0.1.0

**Фактическое исполнение: 2026-10-02 UTC**; дата `2026-10-01` в имени отчёта и
локальных каталогов — идентификатор поручения, не timestamp вызовов.
Статус: операционная проверка завершена, независимое evidence-only review проведено.
Docs commit/push — отдельная операция основного интегратора. Это не новый runtime,
тег или переиздание релиза.

## Источник, установка и границы

- Clean `main` перед проверками: `e1a3edcdbe14ea65847e85d58feba2bed40a79c0`.
  Published annotated `v0.1.0`: object `77079872c76c7bf198339c31de9c47208c58d742`
  → peeled `f7fb04e6768aaf1c04c575166e5b73a5e4e95473`; remote refs реально сверены.
- `src/tests/scripts/pyproject.toml/uv.lock` в Git одинаковы на e1 и f7. CI-only
  отличие e1 — `_TYPER_FORCE_DISABLE_TERMINAL=1`. Raw Windows worktree hashes
  сохранены и не менялись: 32 файла имели только CRLF отличия от canonical Git/LF
  Linux. **Raw cross-platform equality не заявляется**; canonical Git equality
  доказана для 144 source/test/script/manifest/lock файлов, Linux raw bytes = Git.
- Обычная пользовательская установка:
  `C:\Users\User\.local\bin\aimedia.exe`, tool Python/root из обычного uv receipt,
  без tool-root overrides/project venv. PATH отдельно подтвердил этот executable.
  Version `0.1.0`; `direct_url.json`: requested_revision `v0.1.0`, commit_id f7,
  vcs git, noneditable. Все **81 raw package/Git файла** и **25 Windows dependency
  pins** совпали; проверка повторена после install.
- Из public GitHub release скачаны SHA256SUMS и runtime-constraints.txt, checksum
  constraints совпал: `df015e9929c9390f6ba758af9ca26a231b3194d4aa45b23af10b4c008f185947`.
  Idempotent normal install raw0: uv resolved/audited 26 packages и сообщил
  `Installed 1 executable: aimedia`; package/provenance/dependencies не изменились.
  Это **не заявлено как fresh reinstall**. Списки остальных tools до/после совпали;
  upgrade/main/editable/force не использовались.

Фактически выполненная install-команда, cwd — новый owned Windows Temp вне checkout:

```bash
uv --no-config tool install --python 3.12 --constraints <verified-asset-path>/runtime-constraints.txt git+https://github.com/VladimirMonin/aimedia.git@v0.1.0
```

Полный argv с абсолютным asset path и cwd сохранён в локальном receipt; `<...>`
здесь только санитарное сокращение, не готовая команда. Постоянная процедура —
[RELEASE.VerifiedTool](../../../instructions/RELEASE.verified-tool.instructions.md).

## Consolidated offline gates

Один `release` на каждой ОС, без шести повторов режимов. Он включает collect gates,
lint/format/mypy, full pytest с branch coverage и distributions build; tooling tests
внутри full suite покрывают также режимы quick/full/release самого harness.

```bash
uv --no-config sync --locked --offline
uv --no-config run --locked --offline --no-env-file python scripts/quality.py release --report-dir <owned-evidence-path>
```

`UV_OFFLINE=1`, `PYTHONUTF8=1`, `_TYPER_FORCE_DISABLE_TERMINAL=1` заданы явно
(последний совпадает с актуальным CI non-TTY контрактом). Secrets/alias удалены по
именам; harness/socket/child guards и disposable config/data сохранены.

| ОС и cwd/source | Sync raw | Release raw | pytest | Branch coverage | Checks |
|---|---:|---:|---|---|---|
| Windows, clean основной checkout e1 до docs edits, existing `.venv` | 0 | 0 | 1667 PASSED | 93% | 12 обязательных raw0, wheel/sdist build raw0 |
| Настоящий WSL/Linux, существующий clean cache `cli-f7fb04e6-a4b04252`, HEAD f7 | 0 | 0 | 1663 PASSED + 4 SKIPPED | 93% | 12 обязательных raw0, wheel/sdist build raw0 |

Linux skips — только Windows junction cases; **SKIPPED не PASSED**. Assertions,
source/tests/lock/CI не изменялись. Cache проверен перед запуском; новый checkout/env
не создавался. Полные команды, cwd, raw exits/stdout/stderr — в evidence ниже.

## Installed local CLI

Настоящий executable запускался новыми subprocess вне checkout, без `shell=True`,
без source injection и без ключа. Явные fresh `--config`/`--data-dir`;
`config init --file` указал тот же owned TOML. Единственный offline PYTHONPATH —
`tests/offline` для socket guard, не project src.

- Version, root help и command help всех публичных image/jobs/models/providers/config
  команд; models show всех трёх binding, providers/config show/validate/init.
- Все **10 packaged help topics** в human/JSON/raw; history/recent/list/search/costs
  human/JSON. Платные commands не исполнялись ради human smoke: их argv/help и
  failure cases проверены локально, реальные positive calls ниже — JSON.
- Шесть invalid-input cases: missing model/bad format — exit2; unknown model,
  bad resolution/max-images2 — exit3; malformed reference — exit7, каждый один
  error envelope. Это guarded local smoke; transport-level no-submit/security/
  uncertain/recovery/interrupt assertions отдельно покрыты immutable offline suite.

## Разрешённая plain-CLI paid phase

Новый data-root: `artifacts/live/postrelease-v010-20261001/data`, не старые БД.
Перед вызовами fresh бесплатные официальные GET:
[Media create](https://polza.ai/docs/api-reference/media/create.md),
[Nano guide](https://polza.ai/docs/gaidy/nanobanano-2.md),
[model metadata](https://polza.ai/api/v1/models). Exact canonical IDs/selected MIE
prices/modes совпали с выбранными cases; upstream metadata более широкие лимиты
не ослабляли установленный каталог. `only=mie`, fallback=false, async и ceilings
6/11/11 остаются поведением неизменного установленного пакета; same-runtime
contract tests подтверждены offline, **wire body в новом live не наблюдался**.

Ключ получен только через ранее проверенный stdlib reader, `python -I`, memory-only
→ scoped child `POLZA_API_KEY`; descriptor shell не исполнялся. Архивный wrapper
`pi_key.py` SHA `7c182aa12d7f0105960f630e4227fb0113d30a74011577f3bc8035cf0ea3ddc9`
скопирован без изменений; он проверяет SHA underlying audited field reader
`cf3018623cab5be0a30130a1881ac5ab24edf45a92d3616246b35ca5e5ad843a`.
Ключ не показывался reviewer и не записывался в argv/config/evidence. Memory-only
leak comparison собственных CLI outputs, logs/DB/галереи завершился успешно.
Live env не содержал offline/source PYTHONPATH; transport/DNS/TLS/application
не подменялись, observer/mock не применялись.

| Case / local Job | Exact remote model | Mode / refs | Cap RUB | Actual RUB | Final dimensions | CLI raw |
|---|---|---|---:|---:|---|---:|
| Primary / 1 | qwen/image-2.1 | 1K, 1:1, refs0 | 6 | 3 | 1024×1024 | 0 |
| Primary / 2 | google/gemini-3.1-flash-image-preview | 1K, 16:9, refs1 | 11 | 4.8 | 1376×768 | 0 |
| Primary / 3 | openai/gpt-5.4-image-2 | 2K, 1:1, ordered refs2 | 11 | 7 | 2048×2048 | 0 |
| Batch / 4 | qwen/image-2.1 | 1K, 1:1, refs0 | 6 | 3 | 1024×1024 | 0 общий batch |
| Batch / 5 | qwen/image-2.1 | 1K, 1:1, refs0 | 6 | 3 | 1024×1024 | 0 общий batch |
| Deliberate retry of 3 / 6 | openai/gpt-5.4-image-2 | frozen 2K, 1:1, managed refs2 | 11 | 7 | 2048×2048 | 0 |

Все **6 Jobs completed**, каждый имеет один final result. У Jobs1–5 сохранена
также `--keep-original` копия; retry Job6 сохранил только final, без отдельного
original. Итого **11 файлов: 6 final + 5 originals**.
Это **5 paid public CLI invocations**: три generate, один actual batch с двумя
prompt files/`--concurrency 2`, один deliberate `jobs retry 3 --allow-experimental`.
6 remote refs различны; их SHA-256 prefixes (вместо публикации полных IDs):
`8c8a1b87ec36`, `59e7525eda72`, `44c33732c6c8`, `f23be842948a`, `cdb664e743fc`,
`a07bf6f32c6a`. Paid phase началась `2026-10-02T08:09:48Z`; reconciliation/cleanup
завершены `2026-10-02T08:24:33Z`.

### Бюджет: actual и UNKNOWN раздельно

Прежний canonical accounting: **21 attempted POST**, known87.6 RUB + UNKNOWN77 RUB
= conservative164.6 RUB. Источники не переписаны:
`artifacts/quality/f7fb04e6768aaf1c04c575166e5b73a5e4e95473/live-parent/summary.json`
и `artifacts/archive/v0.1.0/aimedia-live-f7fb04e6/ledger.json`.

До первого paid spawn записан новый ledger с тремя primary reservations **28 RUB**
(conservative192.6/200). Замены reserve→actual только после confirmed completed
CLI history и hash/decode artifact checks. Затем batch reserve12:
179.4→191.4→185.4; retry reserve11:185.4→196.4→192.4. Максимум новой программы —
6 submit slots; дополнительные paid вызовы для helper corrections отсутствовали.

Новый actual **27.8 RUB** совпал в CLI costs, readonly SQLite и ledger. Lifetime
known **115.4 RUB**, старый UNKNOWN **77 RUB** остаётся резервом; новых unknown0.
Итого **192.4/200 RUB**, свободно7.6 RUB. Лимит200 не расширялся; 27.8 больше не
estimate, а recorded actual. Старые uncertain Jobs/remote refs не синхронизировались
и не исправлялись. Ни temporal binding, ни автоматический paid retry/fallback.

**Не заявляется новый exact wire POST/GET count или «27 наблюдавшихся lifetime
POST».** Старые21 — historical measured attempts; новые6 — authorized submit slots/
completed Jobs, пять наблюдавшихся paid CLI процессов. Единственность submit и
GET-only sync подтверждены отдельно source-equal offline contracts, не observer
внутри нового live процесса.

## Restart / история / refs / artifacts

После paid phase guarded новые процессы show/recent/list/search/costs подтвердили
statuses/prompts snapshots, Decimal суммы и FTS по prompt, reference filename,
managed reference path, result filename/hash и completed filter. SQLite открывалась
read-only: integrity_check ok, foreign_key_check пуст, migrations1/2/3.

Референсы — две собственные копии прошлых nonsensitive generated images и один новый
synthetic asset. Удалены **только три новые source copies**; archive originals и
все managed inputs сохранены. Retry6 реально завершился с двумя managed refs после
удаления исходников, lineage `{parent_job_id:3,type:retry_of}`. Старые Job3/inputs/
prompt_sources/artifact rows совпали byte-for-value с read-only snapshot до retry.

Проверены **5 managed reference files**, **11 outputs**: decode, size, SHA-256,
dimensions. Guarded completed sync для каждого из шести Jobs — local no-op, после
него show snapshot неизменен; повторение локальных checks не создавало Job/paid call.
Все шесть final images дополнительно просмотрены image-capable read tool: subjects
распознаются; Gemini reference boat и GPT cup/colors видны. Это субъективная scoped
оценка, не сертификация всех режимов или pixel equality.

Удобная локальная gallery: `artifacts/images/postrelease-v010-20261001/index.html`,
рядом11 images и manifest mapping/hash/dimensions/costs. Это копии, не источник
истины. Fresh DB и managed tree сохранены. Exact новый owned Temp cwd удалён после
quiescence/evidence; существующий WSL cache оставлен. Новые `C:/PY/aimedia-*`
каталоги не создавались; default user config/data не использовались.

## Evidence и retained failures

Основной локальный каталог (ignored, **не публичные assets**):
`artifacts/quality/postrelease-v010-20261001/`.

- `*.command.json`, `*.stdout`, `*.stderr`: реальные argv/cwd/raw exits CLI,
  install, provenance и gates. `windows-release/summary.json`,
  `linux-release/summary.json` содержат все 12 check exits на каждую ОС.
- `immutable-bytes.json`, `canonical-git-bytes.json`, `windows-eol-provenance.json`,
  `wsl-byte-equality.json`: untouched source/lock и честная EOL/provenance граница.
- `installed-verification.json`, `tool-provenance*.stdout`, public asset receipts,
  official-selected-models.json: source pin, dependencies и свежие metadata.
- `ledger.json`, `paid-*.command.json`, `postlive-summary.json`,
  `old-job-3-before-retry.json`, source deletion/cleanup/visual/leak-check receipts:
  фактический live/restart/cost/FS результат. Final ledger mutable settlement
  сохраняет исходные caps и started timestamps; это не immutable wire trace.
- `candidate-manifest.json`/`candidate.diff` после docs freeze — точные байты для
  независимого review; final source/lock check сравнивает с исходным manifest.

Сохранены **все обнаруженные helper/proof failures**, не замаскированы как PASS:

1. Первый document-reading Python: UnicodeEncodeError/cp1251, raw1;
   повтор с PYTHONUTF8=1 raw0.
2. Первый constraints parser: marker `sys_platform ==` вызвал ValueError/raw1;
   split-once исправлен в local helper, constraints/deps proof затем успешен.
3. Первое слишком широкое equality assertion включило CI-only diff/raw1;
   source/test/lock равенство доказано отдельно, CI отличие раскрыто.
4. Negative smoke helper ошибочно ожидал malformed-image exit3 вместо contract
   local-file exit7: helper raw1, CLI raw7. Исправлено только ожидание helper,
   project tests не менялись; max-images validation отдельно raw3.
5. Две raw Windows/Linux byte-proof попытки raw1 из-за 32 CRLF-only файлов;
   raw equality не объявлена, final canonical Git/LF proof raw0.
6. Первый postlive helper ожидал выдуманный relation key `retry_of`, KeyError/raw1;
   фактический public relation `parent_job_id/type` проверен повторно raw0.
7. Первый docs validator raw1 сравнил append-only CRLF worktree prefix с LF Git
   blob без учёта EOL; final canonical-prefix check raw0, исторические строки
   не переписывались.

Ни один helper failure не вызвал дополнительный paid submit. Codebase Memory,
Serena и ast-grep отсутствовали в child tool allowlist; использован ограниченный
stdlib AST/read fallback для quality args/offline policy/CLI batch shape, не graph
аудит. Runtime source не редактировался; прежние source audits не перезапускались.

## Остаточные границы и следующий шаг

Catalog остаётся experimental; подтверждены перечисленные три primary modes,
batch и deliberate retry, не все settings/reference limits, account modes или
provider availability во времени. Historical UNKNOWN77 не разрешён. Strict
security/recovery/interrupt coverage здесь offline, не дополнительные paid fault
injections. Нового runtime blocker не обнаружено; helper failures выше раскрыты.

Изменение только docs/runbook/routing. Worker не делал stage/commit/push/tag/release.
Независимый native SOL6.1 reviewer `725d33d9-52e2-48fc-a5ec-b27653393f28`
проверил diff, receipts, бюджет и визуально все шесть final images; P1 не найдено.
Единственное P2 — ошибочное число12 outputs в первоначальном тексте — исправлено
на **11 (6 final + 5 originals)** по неизменённым ledger/postlive/gallery evidence.
Первоначальные writer handoff/freeze и reviewer note сохранены как история проверки,
не переписаны. Reviewer не перезапускал gates/live и не выполнял SQL/hash checker:
эти execution assertions он оценивал по evidence, не как собственный запуск.
Окончательные hashes, проверка исправления и Git delivery receipts сохраняются
интегратором отдельно в `artifacts/quality/postrelease-v010-20261001/parent-delivery/`.
Опубликованный immutable v0.1.0 не перемещать.
