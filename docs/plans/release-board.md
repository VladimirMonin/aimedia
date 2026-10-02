# Release Board — aimedia v0.1.0 🗺️

> [!abstract] Назначение
> Доска фиксирует **фактическое состояние** пути E00–E11: что уже принято и на
> каком SHA, что находится в работе, что ещё не начиналось и какие блокеры
> мешают закрытию. Это статус исполнения, а не доказательство работы программы:
> строка доски всегда указывает источник (commit, файл, команда), а не заменяет его.
>
> Правила честности те же, что в [`README.md`](README.md) и
> [`verification-matrix.md`](verification-matrix.md): план, fixture, mock и
> незакоммиченный файл **не** равны реализованной и принятой функции;
> `NOT_RUN`, `SKIPPED`, `XFAILED`, `NOT_COLLECTED` не равны `PASSED`.

| Поле | Значение |
|---|---|
| Принятый baseline E07–E09 | `8a09d186a650248715b6efcade96b81f69370613` (CLOSED/ACCEPTED offline, не live/release E10) |
| Принятый runtime E06/E10 | `f7fb04e6768aaf1c04c575166e5b73a5e4e95473`; canonical GPT/MIE, один output, все согласованные controls/multiple input refs; [приёмка 2026-10-01](progress/e10-f7fb04e6.acceptance.md) |
| Offline/package/installed | Шесть Windows/Linux quick/full/release raw0; Windows1667 / Linux1663+4 junction SKIPPED; coverage93%; 81 package matches; Windows120 argv, Linux44 console, strict local TLS отдельно |
| Реальная CLI-приёмка | 7 completed Jobs: Qwen1 + GPT6; три tiers/шесть ratios/ref0–3; 14 output files/6 managed refs; 20 new-process history/FTS/cost argv; SOL6.1 540 assertions raw0 + visual supplemental ACCEPT_SCOPED_LIVE_CLI |
| E11 RELEASED | Public [VladimirMonin/aimedia](https://github.com/VladimirMonin/aimedia), [v0.1.0](https://github.com/VladimirMonin/aimedia/releases/tag/v0.1.0), annotated tag→f7, 6 assets, actual public Git-tag uv tool install/81 hashes/25 deps; native SOL6.1 independent311/raw0; GitHub CI Windows/Linux SUCCESS на CI-only76e614f; [отчёт](progress/e11-v0.1.0.publication.md) |
| Остаточные ограничения | Catalog experimental; all15pairs/16refs live не заявлены; multiple outputs отложены CN-05; прежний unknown reserve77 RUB не освобождён. Новых блокеров принятого scope нет |
| Lifetime paid accounting | 21 attempted POST; known87.6 RUB + прежний unknown reserve77 RUB = conservative164.6/200 RUB. Новые7 POST: actual47 RUB; автоматических retries0, старые uncertain Jobs не изменялись |
| Обязательный объём и отложенное | [`release-scope.md`](release-scope.md) + change notes `CN-01`–`CN-05` |
| Требования R01–R24 | [`verification-matrix.md`](verification-matrix.md) |
| Решения развилок E00 | [`decisions/implementation-baseline.md`](decisions/implementation-baseline.md) (D01–D16) |
| Каркас отчёта этапа | [`progress/stage-report.template.json`](progress/stage-report.template.json) |

## Как читать статусы 📖

| Метка | Значение |
|---|---|
| `ACCEPTED` | Этап принят; в приёмке указаны source SHA, прогнанные проверки и независимый review |
| `IN_PROGRESS` | Работа идёт; часть среза может быть незакоммичена — этап **не** принят |
| `PENDING` | Работа не начиналась; реализации, tests и evidence нет |
| `BLOCKED` | Нужно внешнее решение или подтверждённые данные; зависимая работа перечислена ниже |

SHA в таблицах проверяются командами `git cat-file -t <sha>` и
`git log -1 --format=%s <sha>` в текущем clone: это ссылки на существующие
объекты истории, а не цитаты из плана.

## E00–E05: принятые этапы ✅

Источник приёмки различается по этапам и указан в каждой строке: E00 — принятые
baseline-документы (коммиты `643631f`, `0c013f3`) и их trailers; E01–E03 — только
trailers коммитов приёмки `276a334`/`6bfca2f`/`e76d541` и отмеченные чек-листы
`README.md` (отдельных разделов «Приёмка E01»–«Приёмка E03» в `README.md` **нет**);
E04–E05 — разделы «Приёмка E04» и «Приёмка E05» в `README.md` плюс trailers.
Артефакты прогонов (`artifacts/quality/<sha>/`) вне Git и здесь не
воспроизводятся; строка без указанного источника не считается evidence.

| Этап | Результат | Принят (commit) | Проверенный source SHA | Что реально прогнано и чем доказано | Чем приёмка **не** является |
|---|---|---|---|---|---|
| E00 | `release-scope.md`, `decisions/implementation-baseline.md`, `verification-matrix.md` | `643631f` (C00), уточнение `0c013f3` | — (этап документарный; runtime SHA не применим) | Trailers `643631f`/`0c013f3`: review связности документов, Markdown links/YAML/whitespace и `git diff --check` exit 0; runtime-проверки не применимы | Не доказывает архитектуру, реализацию и поведение программы |
| E01 | uv-пакет, Settings, redacted logging, quality harness, offline-изоляция | `276a334` | `e517a158c98e808e6ee796d1dab421ca96c707d1` | Trailer `276a334`: чистый clone: `uv sync --locked --offline` = 0, pytest 95 passed, quality quick/full = 0, Ruff/mypy/build = 0; нативный SOL6 E01 `PASS` | Нет CLI-команд генерации, истории и provider |
| E02 | Domain, ports, prompt/input preparation, fake provider | `6bfca2f` | `453689fc86edd98acb6270ebac66fbbeb9974a8d` | Trailer `6bfca2f`: чистый clone: offline pytest 339 passed, quality quick/full = 0, Ruff/mypy = 0; SOL6 E02 `PASS` | Не доказывает ни HTTP-контракт, ни реальный Polza |
| E03 | Registry YAML, aliases, effective model validation | `e76d541` | `4a068c510194f386c17a7221d4f40232b669728d` | Trailer `e76d541`: чистый clone: offline pytest 478 passed, quality quick/full = 0, Ruff/mypy/build = 0; SOL6 E03 `PASS` | Встроенный production-каталог **пуст**: реальные model IDs/лимиты не подтверждены |
| E04 | SQLite schema + один migration runner, repositories, точные деньги | `7fecf5f` | `e13b56921844910f15bc6d5037d993d48d65442e` | `README.md` «Приёмка E04» + trailer `7fecf5f`: чистый clone 81 целевой и 561 полный offline-тест, Ruff, mypy, quality quick/full/release, CLI smoke, сборка wheel/sdist — exit 0; SOL6 `PASS` | Backup — согласованный **ручной** протокол ([`backup-contract.md`](backup-contract.md)), не команда; cost/usage после remote success проверены симуляцией без live provider |
| E05 | Managed artifacts, проверка байтов, конвертация PNG/JPEG/WebP, no-clobber публикация, финальная запись Job | `7912bc9` | `701d2d830c9e8a8f636561a76f0692bc5b23eee3` | `README.md` «Приёмка E05» + trailer `7912bc9`: чистый clone offline quality quick/full/release по 716 тестов, Ruff, mypy, сборка и CLI help/version — exit 0; SOL6 `PASS` | Нет live Polza, полного Job workflow и CLI `--keep-original` (это E06–E09) |

Границы принятого состояния:

- E00–E05 закрывают **офлайн-каркас** image-only CLI. Ни один этап не подтверждает
  реальную генерацию, production model IDs, стоимость вызова или установленный
  пользовательский пакет.
- Схема SQLite на E04 — версия **v1**. Новые данные сверх v1 оформляются миграцией
  v2 по `CN-01`, а не правкой v1.

## E06: Polza adapter и проверенные bindings — `ACCEPTED` на f7 🔌

[Scoped source/offline/installed/live приёмка](progress/e10-f7fb04e6.acceptance.md):
Qwen и canonical GPT/MIE, реальные settings/ordered refs/стоимость. Каталог не
активирован blanket stable; границы отдельных режимов перечислены в отчёте.
Следующие C09 строки — исторические приёмки подсрезов, не текущие блокеры.

### Что уже committed

| Срез | Commit | Содержимое | Evidence из trailer |
|---|---|---|---|
| C09a | `5979189` | Сопоставление image request со схемой Media API (remote model ID, prompt, порядок refs, лимиты до base64) | 804 offline passed; 88 targeted contract passed; quality full (Ruff lint/format, mypy, pytest) exit 0; SOL6 review OK |
| C09b | `628af33` | Чистый response parser: статусы, безопасные URL/ID, `Decimal` (`cost_rub` приоритетнее alias), unknown ≠ zero | 938 offline passed; 134 targeted contract passed; quality full exit 0; SOL6 review OK |
| HTTP runtime lock | `b99dc25` | `httpx` 0.28.1 и зависимости зафиксированы после разрешённой загрузки публичных PyPI wheels | Чистый clone: `uv sync --locked --offline` и quality quick, 938 тестов, exit 0 |
| C09c1 | `c794c0bcf0356b88e1388202b9717016b2d7ad95` | Один `POST` submit и отдельные `GET` status/result через инжектируемый client; safety-cap, безопасный ID, redaction и `SUBMIT_UNCERTAIN` для неопределённого платного исхода; инструкция [`PROVIDER.polza-media`](../../instructions/PROVIDER.polza-media.instructions.md) | Чистый clone **этого SHA**: `uv sync --locked --offline` = 0, quality full = 0 (1019 offline passed, Ruff/mypy = 0); SOL6 `E06 C09c1 PRECOMMIT: OK` по предкоммитному diff. Это приёмка **среза**, не всего E06 |
| C09c2 | `2110a5fe5c4547a40f438f9e965ff15632a95e7c` | Отдельный downloader сырых image-байтов: только документированный `s3.polza.ai`, закреплённый публичный IP при TLS hostname, без bearer/proxy/redirect/retry, ограниченные DNS-ожидание и поток, безопасные ошибки и DEBUG-журнал; прямая зависимость `httpcore` и правило «личный CLI» в `AGENTS.md` | Чистый clone **этого SHA**: `uv sync --locked --offline` = 0, quality full = 0 (**1145 offline passed**, Ruff/mypy = 0); SOL6 `E06 C09c2 PRECOMMIT: OK` по предкоммитному diff. Только срез: downloader ещё не подключён к Job, live `NOT_RUN` |

Committed contract-тесты: `tests/contracts/test_polza_requests.py`,
`test_polza_responses.py`, `test_polza_costs.py`, `test_polza_gateway.py`,
`test_polza_download.py`; security — `tests/security/test_download_auth.py`.
Summary clean-clone gate хранится локально вне Git в
`artifacts/quality/2110a5fe5c4547a40f438f9e965ff15632a95e7c/summary.json`
(для C09c1 — в каталоге его SHA); воспроизводимый источник — commit SHA и команда.

### Исторические ограничения C09 (состояние на C09c2, не текущий каталог)

Следующие записи сохранены как история C09c2. На `8a09d18` downloader уже
подключён к Job/CLI, Qwen/Gemini YAML и опубликованные тарифы добавлены;
живые режимы всё ещё не проверены. Текущий MIE count slice добавляет третью
experimental запись по публичным Markdown, paid POST = 0, MCP refusal не обходился.

### Что ещё должно появиться до закрытия E06 (историческая запись C09c2)

- Production bindings и верхние цены не подтверждены: встроенный каталог остаётся
  пустым (ограничение E03). Официальные гайды называют
  [`qwen/image-2.1`](https://polza.ai/docs/gaidy/qwen-image-2.1.md) и
  [`google/gemini-3.1-flash-image-preview`](https://polza.ai/docs/gaidy/nanobanano-2.md)
  с референсами, но не доказывают живую доступность конкретных режимов. Публичные
  каталоги [Qwen](https://polza.ai/models?search=qwen%20image%202.1) и
  [Gemini](https://polza.ai/models?search=gemini%203.1%20flash%20image) на 2026-09-30
  показывают только цены **«от»** (3 ₽ / 4,8 ₽ соответственно), а не верхнюю цену
  выбранного режима; платный POST не выполнялся.
- `tests/contracts/test_polza_errors.py` из плана E06 пока отсутствует: ошибки
  gateway/downloader проверяются существующими contract/security-тестами; необходимость
  отдельного файла оценивается по покрытию требований, а не по имени fixture.
- `tests/live/test_polza_smoke.py` и `tests/live/` — каталога нет; live-прогон не
  выполнялся, ключ не читался.
- Подключение downloader к Job, проверка/сохранение его байтов и recovery — E07–E08,
  не доказаны срезом C09c2. Приёмка wheel/sdist и installed-CLI smoke — слот E10.

## E07–E09: ACCEPTED на `8a09d18`; E10 ACCEPTED на f7; E11 RELEASED ✅

Fresh whole-source review `32e31e48` и correction review `c4b4f239` — OK.
Evidence baseline: `artifacts/quality/8a09d186a650248715b6efcade96b81f69370613/`
(`windows`, `linux`, `installed-ux-parent`). Windows clean release **1366 PASSED**,
12 raw exits 0; Linux **1362 PASSED + 4 Windows-only junction SKIPPED**, 12 raw exits 0
(SKIPPED ≠ PASSED). Fresh installed receipt `a69473fc84`: 62 console / 22 injected argv,
80 matched bytes; actual SIGINT/restart/kernel locks/GET-only sync проверены.
Schema v2 archive и v3 FTS committed; ownership platform fix входит в baseline.
Это приёмка E07–E09, не закрытие E06 live, E10 или E11.

| Этап | Что должно появиться | Зависимости | Критерий приёмки (из [`README.md`](README.md)) | Состояние |
|---|---|---|---|---|
| E07 | Одиночный application use case `single_image.generate_image`: prepared inputs → confirmed CREATED → validation/archive → один submit → ref/polling → billing → все artifacts → confirmed history | E04–E06 | `tests/integration/test_single_image.py`: реальная tmp SQLite/managed copies/Pillow, fake + Polza MockHTTP, ошибки без duplicate submit; independent reviews и clean release baseline подтверждены выше | `ACCEPTED / CLOSED` на `8a09d18`; evidence выше |
| E08 | Batch с ограниченной конкурентностью, partial failures, `retry`/`sync`, Ctrl+C, recovery и защита от двойного Runner | E07 | Peak active = лимит, partial outcome, submit_count = 1 при неизвестном исходе, restart без нового POST, cross-process guard | `ACCEPTED / CLOSED` на `8a09d18`; evidence выше |
| E09 | Полный CLI, JSON/exit codes, история, лексический FTS5, валютные сводки, atomic help | E03, E08 | Реальный argv, JSON/exit для текущей попытки, поиск prompts/ref provenance/managed paths/hashes/result artifacts после restart без чтения файлов, RUB/USD/unknown раздельно, packaged help вне cwd | `ACCEPTED / CLOSED` на `8a09d18`; evidence выше |
| E10 | Приёмка release candidate: Windows+Linux, offline quick/full/release, wheel+sdist, installed smoke, secret scan, агентский walkthrough, ограниченный live | E09 | Один frozen runtime SHA; [OS/package/UX/TLS/live и независимая приёмка](progress/e10-f7fb04e6.acceptance.md); 4 Windows-only Linux skips раскрыты, не PASS | `ACCEPTED` на `f7fb04e6`; не публикация E11 |
| E11 | Публикация проверенного релиза | E10 + отдельное разрешение владельца | Annotated tag на проверенном SHA, wheel/sdist + checksums, установка по тегу с проверкой происхождения | `RELEASED`: public VladimirMonin/aimedia/v0.1.0, tag→f7, 6 assets, actual tag-install/81 hashes; [приёмка](progress/e11-v0.1.0.publication.md) |

**Историческая запись одиночного среза до укрупнения (не текущий статус):**
E07 содержал частичное одиночное ядро и `tests/integration/test_single_image.py`;
локальные raw attempts — `artifacts/quality/e07-single-sol61-writer/`, не замена
независимому review и committed clone. Follow-up P1/P2: published snapshot и исходный
interrupt сохраняются при unknown final commit, COMPLETED не перезаписывается
устаревшим FAILED; output preflight выполняется до submit. Локальный dirty-worktree
focused — 153 passed, full — 1303 passed (11 raw codes = 0); новые и предыдущие
попытки сохранены отдельно в `artifacts/quality/e07-review-fixes-sol61/`. Независимый
review исправлений ещё требуется. CLI/batch/recovery/locks, production
activation и live не реализованы этим срезом. E08–E11 не начаты; названия будущих
тестов на доске не являются признаком их существования.

## Дополнительная работа по изменению объёма (CN) 🧩

Принятый объём расширен change notes в [`release-scope.md`](release-scope.md);
эти строки не являются реализованной функцией.

| ID | Работа | Зависимости | Что докажет приёмка | Состояние |
|---|---|---|---|---|
| CN-01 | Долговечные managed-копии reference images, связанные с Job (path/SHA-256/MIME/размер/позиция) в managed-дереве app data | E02 (inputs), E04 (storage v1) | Новая миграция **v2** без правки schema v1; no-clobber/ownership; Job→ref связь читается после удаления исходника; negative-тесты; managed-копии входят в quiesced backup-манифест. Контракт — [`07-storage-history-costs.md`](07-storage-history-costs.md), «Managed-копии reference images»; реализован первый code-срез: поле `InputRef.managed_path` и таблица v2 `managed_input_copies` со связью в repository. Файловый шов добавляет snapshots, `inputs/` no-clobber publish/resolve, confirmation hook и tmp manual backup/restore тест; одиночная application/CLI/history/recovery композиция committed и принята на `8a09d18` | `ACCEPTED` offline на `8a09d18`; v2 archive и CLI/recovery реализованы, backup CLI не входит |
| CN-02 | Лексический FTS5 и фильтры остаются обязательными; семантический/векторный поиск — backlog следующего релиза | E09 для FTS5 | FTS5/фильтры находят Jobs/prompts/ref-метаданные/artifacts; FTS5 не выдаётся за семантический поиск | `ACCEPTED` offline FTS v3 на `8a09d18` (semantic — вне v0.1.0) |
| CN-03 | Audio/speech/STT/TTS — отдельное приложение, вне планов и навигации этого репозитория | — | В `docs/plans/` нет речи как задачи aimedia; отменяемые положения `01` (раздел «Расширение на аудио» и примеры `aimedia audio ...`) явно superseded с сохранением исходного текста; ссылки не битые | `DONE` (документарно) |

**Исторические подсрезы CN-01 до общей приёмки `8a09d18`:** ограничения ниже
относятся к указанным старым SHA, не к текущему CLI/v3.

**Проверенный подсрез CN-01 — DTO/SQLite:** source SHA
`a237d4f7e485d37a92f1467a490b74413537dcb1`; production v1→v2, сохранность связей и
оба P1 проверены. Свежий native Sol 6.1 review — `PRECOMMIT: OK`; parent full и
full чистого клона — **1192 passed**, все 11 raw exit codes — 0 (Windows).
Evidence вне Git: `artifacts/quality/a237d4f7e485d37a92f1467a490b74413537dcb1/`
(`summary.json`, `clone-receipt.json`). Linux/release/live — `NOT_RUN`; это не
приёмка файловых managed-копий, всего CN-01 или E06/E07.

**Проверенный файловый source-срез CN-01:**
`aaf1c5ede8a3c9df768b020ce7ab8dc0ca6a5265` — snapshot одного чтения,
`LocalManagedInputStorage`, `archive_reference_images`; DDL v1/v2 и repository
не изменены. Добавлены **60** offline cases: реальные FS+SQLite copies/reopen/resave,
удаление/изменение источника, no-clobber, traversal, symlink/Windows junction,
partial publish/rollback/possible commit/неверное подтверждение/tamper/oversize,
реальный Polza MockHTTP и ручной quiescent DB+inputs+outputs manifest/restore.
Fresh native Sol 6.1 review `b946762e` — **PRECOMMIT: OK**. Parent full и full
чистого клона — **1252 passed**, все **11** raw exit codes 0 (Windows); clone,
locked/offline sync и quality raw 0, status до/после gate чистый.
Evidence вне Git: `artifacts/quality/aaf1c5ede8a3c9df768b020ce7ab8dc0ca6a5265/`
(`summary.json`, `clone-receipt.json`). Ранние failed attempts/raw codes сохранены
в `artifacts/quality/cn01-files-raw-codes.json`; timeout `fc99331f` и output-contract
failure `b04f6e24` не считаются успешными workflow.
Полные E07/CLI/D03/history show/retry/sync и FTS — **NOT IMPLEMENTED**;
Linux/release/live и реальные пользовательские данные — **NOT_RUN**.
Это source-приёмка файлового шва, не приёмка всего CN-01 или E06/E07.

## Зависимости и блокеры 🚧

Исторические B3/B6 зависимости offline интеграции сняты приёмкой `8a09d18`.
E06/E10 закрыты scoped приёмкой f7; E11 RELEASED после явного разрешения владельца.
Незакрытых блокеров принятого scope нет; неизвестные исторические расходы77 RUB
сохранены, не превращены в actual/zero.

| ID | Блокер | Что блокирует | Что требуется, чтобы снять | Владелец |
|---|---|---|---|---|
| B1 | **RESOLVED scoped:** f7 Qwen1 + GPT6 реальных completed CLI Jobs, live settings/refs/cost/restart проверены; старый Gemini uncertain не переписан | — | Границы live vs offline — [приёмка](progress/e10-f7fb04e6.acceptance.md); никаких retries старых uncertain | исполнитель |
| B2 | **RESOLVED:** C09c1/C09c2 и их интеграция приняты; same-SHA f7 source/offline/installed/live подтверждены | — | E06 scoped ACCEPTED | исполнитель + интегратор |
| B3 | **RESOLVED:** E07–E09 baseline и f7 async/canonical/single/settings/refs приняты source/offline/live | — | Fresh SOL6.1 source + evidence review; повтор принятого аудита не требуется | интегратор |
| B4 | **RESOLVED:** владелец отдельно разрешил public repo/push/tag/release; опубликован VladimirMonin/aimedia/v0.1.0 с tag→f7 | — | Actual public metadata/tag/assets/install и independent311 подтверждены [E11](progress/e11-v0.1.0.publication.md); login сам по себе по-прежнему не authorization | владелец |
| B5 | Финитный план выполнен: conservative164.6/200 RUB, включая прежний reserve77; новые actual47 RUB | Новые, ещё не разрешённые платные вызовы | Остаток35.4 RUB не является разрешением расширять план | владелец |
| B6 | **RESOLVED offline:** CN-01 v2 archive и полная E07/CLI/D03/history/recovery композиция приняты на `8a09d18`; v3 FTS committed | — | Backup CLI отсутствует по scope, live отдельно | исполнитель |

**Public catalog metadata (два GET без ключа, paid POST = 0):**
`qwen/image-2.1`: `image_resolution` 1K → **3 ₽**, 2K → **6 ₽**;
`google/gemini-3.1-flash-image-preview`: 1K → **4,8 ₽**, 2K → **7,2 ₽**,
4K → **10,8 ₽**. Для обеих записей `currency=RUB`, `unitParam` отсутствует;
консервативные верхние ставки по всем опубликованным tiers — **6 / 10,8 ₽**.
Источник — `GET https://polza.ai/api/v1/models/catalog`, HTTP 200; evidence вне Git:
`artifacts/metadata/polza-catalog-qwen.json`, `polza-catalog-gemini.json` рядом.
Каталог подтверждает 1:1/16:9, Media endpoint и наличие image inputs; это **не live**.
Для Gemini guide (8 refs) и catalog (14) расходятся: до отдельной сверки не заявлять
14; committed binding ограничен подтверждённым пересечением (не более 8).
Непроверенные production bindings не активированы; ключ не читался.

`B4` снят явным разрешением и выполненной E11-приёмкой. Бюджетный reserve и
ограниченная live-матрица остаются честно раскрытыми ограничениями, а не поводом
повторять старые uncertain Jobs или объявлять все модели stable.

## Критерии релиза (ворота G0–G7) 🚦

| Ворота | Критерий | Текущее состояние |
|---|---|---|
| G0 | Нет незакрытого решения, от которого зависит реализация | E00 baseline и CN-01–CN-05 приняты; отдельная publication authorization получена и исполнена |
| G1 | Функции согласованного scope существуют и интегрированы | ACCEPTED f7: один output/multiple input refs/all agreed controls; catalog experimental раскрыт, multi-output deferred |
| G2 | Обязательные offline-assertions выполнены на кандидате | f7: Windows1667 PASSED / Linux1663 PASSED + 4 Windows-only SKIPPED; шесть quick/full/release raw0; coverage93% |
| G3 | Есть live evidence заявленных моделей/режимов | f7: Qwen1 + GPT6 completed; реальные параметры/refs/files/cost/restart; не blanket all-models/all-pairs claim |
| G4 | Собранные distributions и установленный пакет работают вне checkout | f7: wheel/sdist/81 raw package matches; Windows/Linux noneditable install, console/UX/TLS receipts |
| G5 | Reviewer проверил evidence на одном SHA | f7: source03264ffd OK; live d13590c2 540 assertions raw0 + supplemental31bdf4f0 ACCEPT_SCOPED_LIVE_CLI |
| G6 | Annotated tag указывает на проверенный SHA | PASSED: public v0.1.0 peeled→f7, independent tag-object/Git ls-remote checks; tag не перемещён |
| G7 | Личная копия установлена по тегу с проверяемым происхождением | PASSED: actual uv tool install public v0.1.0, noneditable direct_url commitf7/81 hashes/25 constraints; personal C:\Users\User\.local\bin\aimedia.exe, guarded console smoke0 |

## Слоты доказательств 📦

Для каждого ещё не принятого этапа заполняются **все** слоты; пустой слот
означает незакрытую проверку, а не «почти готово».

| Этап | `source_sha` | `tree_clean` | `quality quick/full/release` | Независимый review | `evidence_path` |
|---|---|---|---|---|---|
| E00 | — (документарный) | не применимо (docs-only) | `NOT_RUN` (не применимо) | не требуется | baseline/release-scope + trailers `643631f`/`0c013f3` (runtime evidence не применимо) |
| E01 | `e517a158…` | да (чистый clone) | `PASSED` (quick/full) | SOL6 `PASS` | trailer `276a334`; артефакты вне Git: `artifacts/quality/<sha>/` |
| E02 | `453689fc…` | да | `PASSED` (quick/full) | SOL6 `PASS` | trailer `6bfca2f`; артефакты вне Git |
| E03 | `4a068c51…` | да | `PASSED` (quick/full) | SOL6 `PASS` | trailer `e76d541`; артефакты вне Git |
| E04 | `e13b5692…` | да | `PASSED` (quick/full/release) | SOL6 `PASS` | вне Git |
| E05 | `701d2d83…` | да | `PASSED` (quick/full/release) | SOL6 `PASS` | вне Git |
| E06 | `f7fb04e6768aaf1c04c575166e5b73a5e4e95473` | да, frozen source/clean clones | Windows/Linux quick/full/release raw0; 1667 / 1663+4 disclosed Windows-only skips; live7 completed | SOL6.1 source03264ffd + live540/raw0/supplemental31bdf4f0 ACCEPT | [приёмка](progress/e10-f7fb04e6.acceptance.md), локально `artifacts/quality/<f7-sha>/live-parent/` |
| E07–E09 | `8a09d186a650248715b6efcade96b81f69370613` | да | Windows release 1366 PASSED; Linux 1362 PASSED / 4 SKIPPED, все 12 raw 0 | `32e31e48` / `c4b4f239` OK | `artifacts/quality/<sha>/{windows,linux,installed-ux-parent}` |
| E10 | `f7fb04e6768aaf1c04c575166e5b73a5e4e95473` | да, runtime source/clean clones | шесть OS gates/package/install/UX/TLS +7 live completed +20 restart argv | Native fresh source/Windows/Linux UX/live reviewers, scoped ACCEPT | [приёмка](progress/e10-f7fb04e6.acceptance.md), локально `artifacts/quality/<f7-sha>/` |
| E11 | `f7fb04e6768aaf1c04c575166e5b73a5e4e95473` tag runtime; docs29c/CI-only76e614f не новые release payloads | source/tag clean; runtime unchanged | E10 same-SHA gates сохранены; actual publication/tag-install0; GitHub full Windows/Linux SUCCESS на76e614f | Native fresh SOL6.1 independent311/raw0 + own Linux focused1/0/0; source не переаудирован | [приёмка](progress/e11-v0.1.0.publication.md), `artifacts/quality/<f7-sha>/publication-parent/` и `artifacts/archive/v0.1.0/` |

Поля заполняются по [`progress/stage-report.template.json`](progress/stage-report.template.json);
строка R01–R24 в [`verification-matrix.md`](verification-matrix.md) получает реальный
pytest node ID и статус только после прогона на конкретном SHA.

## Границы достоверности 🔬

- E06/E10 приняты scoped на f7 по [новому отчёту](progress/e10-f7fb04e6.acceptance.md),
  не по старым C09 receipts. Ни local commit, ни E10 не означают публикацию E11.
  Исторический commit C09c2,
  clean-clone full gate и SOL6 предкоммитный review доказывают только gateway и
  безопасное скачивание по документированному URL, а не интеграцию Job, production
  bindings или release candidate.
- Documented experimental model IDs/тарифы подтверждены источниками; YAML-запись и fixture не
  доказывают живую поддержку.
- `SKIPPED`, `XFAILED`, `NOT_RUN`, `NOT_COLLECTED` не равны `PASSED`; строка без
  `PASSED` означает незакрытое требование.
- Статус `DONE` для `CN-03` относится только к документарному выводу speech из
  навигации и не означает изменений кода.

## Исторический MIE count checkpoint: до source76 review

Новый локальный slice от `8a09d18`, не accepted E06/live: третий data-only
`gpt-5-4-image-2-mie` → exact `openai/gpt-5.4-image-2@mie`, experimental /
NOT_LIVE_VERIFIED, text-only 1K. `max_images` 1–4/default 1 → `input.n`, один
Job/POST; Qwen/Gemini >1 fail-closed. Public guide/model Markdown 2026-09-30;
RUB 4/image только MIE 1K, не total Job/default-openai token tariff; unitParam
не выдуман. Reference URLs/higher resolutions известны API, но не включены/не
проверены здесь. Paid POST = 0, MCP refusal не обходился.

Dirty-worktree Windows release checkpoint: **1417 PASSED / 93% coverage**, все
12 raw exits 0; focused **394 PASSED**, static mypy `--platform linux` raw 0.
Evidence: `artifacts/quality/mie-count-sol61/` (`release-checkpoint/summary.json`,
`focused-accepted.*`, `mypy-linux.*`, `checkpoint-manifest.json`). Ранние failures
не скрыты: absent asyncio marker, ошибочные view/exit assertions, missing recovery
eligibility для нового error code (исправлено), прежняя surrogate body assertion
(сохранена без ослабления). `focused-third.*` / `focused-final.*` — raw 1.
Infrastructure timeout1200000ms — FAILED, не PASS; resume не меняет модель/протокол.

Required-count n2→1/n4→2 относится к пригодному normalized result: durable ref и
billing до download сохраняются, COMPLETED запрещён, recovery GET-only. n2→0 —
отдельный malformed C09 result: POST SUBMIT_UNCERTAIN / GET invalid-response;
новый billing unknown, не zero, прежний known ref/cost не стирается.
Fresh reviewer, final-SHA Linux runtime/installed acceptance и live — NOT_RUN;
parent владеет staging/commit/frozen-SHA проверками. E10/E11 не закрыты.

## Исторический source76 и финансовый fix перед первым live POST

На source `76f93a2c1206ae5ced45bc47507578cf1e45775e` parent подтверждает native
precommit source-review `4fc`: accepted, **1417 PASSED**. Windows/Linux same-SHA
quick/full/release — все шесть raw 0; независимый installed **112 argv** и strict
TLS — raw 0. Это offline source76, не live. Исторический checkpoint выше сохранён
со своими тогдашними pending/NOT_RUN, не описывает текущий review статус.

Новый fixed Media provider DTO для трёх existing bindings — pending current
independent review/commit/frozen-SHA gates; source76 gates не доказывают новые байты.
Цены Registry относятся к выбранному MIE, не automatic Gemini upstreams.
API only=[mie]/fallbacks=false/ceil-RUB price filter не гарантирует actual billing,
Job total или 200 RUB. Live **STOP BEFORE FIRST PAID POST**: paid POST = 0,
credential reads = 0; E06 live/E10/E11 остаются открытыми. Public-doc evidence:
`artifacts/metadata/polza-media-priced-routing-2026-09-30.json`; writer gates:
`artifacts/quality/priced-media-writer/` (отдельно от source76).

## Исторический e751 baseline и documented async candidate

Источник следующих **e751** фактов — задание владельца с same-SHA acceptance и
live observations; writer этого среза не читал private receipts/keys/userdata и
не повторял прежний OS/installed аудит. Count/price guard принят offline на
`e751f97633eea5c33b8831186dad98d0af5f422b`: шесть actual-OS quick/full/release
raw 0; Windows **1468 PASSED**, Linux **1464 PASSED + 4 junction SKIPPED**
(не PASSED); installed Windows **116 argv**, Linux **18 smoke**, TLS verified.
Эти результаты не распространяются на новые async bytes.

| Наблюдение на e751 | POST | Outcome | Деньги / предел доказательства |
|---|---|---|---|
| Qwen no-ref 1K 1:1 | 1 | COMPLETED, real PNG + WebP 1024, restart verified | Known actual 3 RUB; только этот режим, не все refs/resolutions и не async |
| Gemini ref 1K 16:9 (Job2) | 1 | SUBMIT_UNCERTAIN после ~30.26s, remote_ref/cost отсутствуют | Unknown private outcome/fee; reserve 11 RUB, причина не доказана; Job2 не повторять |

**total attempted POST 2, known cost 3 RUB, uncertain reserve 11 RUB**;
lifetime accounted 3 + 11 ≤ cap 200 RUB. Это не collective PASSED, резерв не
обнуляется и не выдаётся за фактическую цену. Старые paid0 записи выше остаются
историческими записями своих SHA.

Принятый dialogue `16f9`: минимальный top-level boolean `async: true` для тех же
трёх exact fixed-provider bindings до final serialized cap; routing/Decimal/count/
references неизменны, caller override запрещён. Canonical pending/processing →
durable exact ref до GET; immediate completed работает. TaskId-only/непригодный
конверт остаётся uncertain, parser не расширяется, POST fallback нет.
Owner — PROVIDER.polza-media; уточнение — baseline D10.

Новый source SHA **NOT_COMMITTED**, независимый review **PENDING**, async live
**NOT_RUN**; candidate offline evidence — `artifacts/quality/media-async-writer/`
(фактические raw codes/manifest, не переиспользованные e751 runs). Registry остаётся
experimental без activation. **E06/E10 OPEN; E11 OPEN / publication permission
absent**. Paid/live STOP: нужны отдельное разрешение, reviewed committed/frozen
SHA, применимые same-SHA OS/install/observer proofs и bounded plan; новые POST,
retry Job2 и угадывание его ref по временной/model близости запрещены.

## Ссылки 🔗

- [`README.md`](README.md) — карта плана, этапы, коммиты и runbook релиза.
- [`release-scope.md`](release-scope.md) — обязательный объём, отложенное и change notes `CN-01`–`CN-03`.
- [`verification-matrix.md`](verification-matrix.md) — R01–R24, владелец этапа и слоты evidence.
- [`decisions/implementation-baseline.md`](decisions/implementation-baseline.md) — D01–D16.
- [`backup-contract.md`](backup-contract.md) — quiescent offline backup (managed files).
- [`logging-contract.md`](logging-contract.md) — диагностические события и redaction.

### Историческая запись: Consolidated writer verification, 2026-09-30

E07–E09 остаются **IN_PROGRESS candidate / whole-review pending**, не ACCEPTED.
Публичный image CLI, batch, retry/sync, ownership, v3 FTS search, costs и 10 packaged
help topics реализованы вместе. Locked offline full: **1344 passed / 93% branch
coverage**, collection/Ruff/mypy/pytest raw exits **0**;
`artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/summary.json`.
Ошибочные ранние gates сохранены в `full/`, `full-final/` с raw exit 1.
Cached offline wheel/sdist build и no-deps target install прошли; установленный
entrypoint/resources outside cwd проверен с явными disposable config/data paths
(`installed-smoke-03.txt`, raw exit 0). Приватный config isolation incident и
санкционированный exact-template cleanup не скрыты. Финальная release/build evidence
лежит в том же writer report directory. Live, Linux, E10/E11 и независимое принятие
не заявлены; staging/commit/push/tag/release не выполнялись.


### Историческая запись: Consolidated corrective candidate E07–E09

Семь source findings исправлены одним связанным срезом: sync current-attempt errors,
config-test isolation, DB leaf preflight, storage error boundary, parser format choices,
DB-only reference/result FTS v3 и batch SIGINT regression. Accepted v1/v2 DDL неизменен.
Добавлено **22** offline cases: focused **69 passed**, итоговый release **1366 passed /
93% coverage**, все **12 raw codes 0**, build успешен, SKIP/XFAIL нет.
Evidence: `artifacts/quality/whole-cli-consolidated-fixes/release/summary.json`;
первый raw-1 release сохранён отдельно в `release-first-failed/` (исправлена старая
assertion raw ORM error → StorageError для directory leaf).
Код остаётся uncommitted candidate до fresh independent review и parent SHA/clone gate.
Linux/live/clean clone/немодифицированный installed loopback NOT_RUN. Отсутствующий
официальный unit parameter для нескольких outputs не придуман; returned images
сохраняются все, multi-output request live остаётся NOTPROVEN. Stage/commit не выполнены.


## Исторический follow-up до CN-05: Media DTO/count + безопасная HTTP-ошибка

Исходный source `8de7a10009b8198209320aa70c26e49540e7f1ed`: исторические четыре
live count=1 приняты отдельно; старый MIE n=2 failed/not proven. Новый fix-set
не наследует эти receipts. По parent direct probes qualified-n HTTP400
BAD_REQUEST относится к непризнанному @mie qualifier **нового diagnostic запроса**;
причина старого MIE Job5 не устанавливается задним числом. Canonical base-n дал
completed с одним image/output_units=1/actual cost4 при n=2; base-max дал те же
один image/output_units=1/cost4 при max_images=2. Ни один case не доказал
count=2 — live blocker сохраняется.

Кандидат меняет только remote binding на canonical base, Media count поле на
input.max_images и whitelist diagnostics HTTP-ошибки через persisted/current CLI.
MIE only/noFallback/cap4/async=true и Registry subset остаются. Старые Job2/Job5
не изменяются/не повторяются; history binding сохраняется без миграции.
Решение/разрешённые поля — change notes в implementation-baseline; regressions —
Polza count/priced routing/error diagnostics + single image/current CLI/reopen.
Consolidated release gate и независимый review кандидата ещё не приняты.
Новый source NOT_COMMITTED / NOT_LIVE_VERIFIED; E06/E10 OPEN, обязательный
count=2 NO WAIVER, E11 not authorized. Parent direct HTTP probe не source CLI
и не frozen SHA evidence. Предыдущие датированные pass/fail записи сохранены.


Final scope подтверждён отдельно: canonical Media + DTO max_images + safe error,
NO Images adapter. Parent Images qualified/base probes также не доказали count=2:
HTTP400 для qualifier; canonical top-level n=2 — HTTP200 legacy created/один image/
cost4/без id. Это диагностические observations, не новая routing authority.
Отдельный parent Seedream4/max_images=2 probe также дал один image/cost3;
это не новый binding и не count=2 proof этого source.
На checkpoint после шести новых probes lifetime 13 POST, known33.6 + unknown77 =
110.6/200 RUB. Явное разрешение пользователя на diagnostic repeats supersedes
прежний MIE STOP для probes, но не разрешает автоматический retry Job/DB writes
или публикацию. Child выполняет только OFFLINE release/freeze; E06/E10 OPEN.


## Исторический checkpoint CN-05 до f7: single/settings/multiple input references

Прямое решение владельца отложило несколько выходных изображений одного запроса.
Прежний count2 mandatory/NO WAIVER gate отменён по принятому scope, не как QA waiver.
Все входные refs + prompt обязательны; batch отдельных Jobs не отложен.
Кандидат объединяет reviewed safe-error/canonical-ID fix с включением всех MIE
controls: один YAML enums/defaults/лимиты,15 допустимых setting pairs,prompt≤5000,
refs≤16 PNG/JPEG/WebP; нет выдуманного byte-limit/quality/seed. Only MIE/noFallback/
async и exact Decimal guard сохраняются, GPT cap11. Старые multi-output snapshots
и generic finalizer/GET-only sync не удаляются.

Parent direct single2K16:9 + two local PNG/JPEG refs succeeded/cost7 after known-ref
continuation (один GET,ноль POST). Source observation:
`single-2k-two-refs-poll-known-http.json` в parent probe root. Original180s processing
capture unchanged; задержка не основание повышать HTTP30/default wait. Это remote
proof одного режима, не source/installed CLI acceptance нового SHA.
Lifetime checkpoint14POST,known40.6+unknown77=117.6/200. Multiple-output probes
stopped; authorized single/settings live остаётся у parent. NOT_COMMITTED /
NOT_LIVE_VERIFIED,E06/E10 OPEN для CN-05 scenarios,E11 not authorized;fresh review
и frozen gate required. Прежние датированные evidence/отчёты не переписаны.


## Postrelease verification — выполнено 2026-10-02 UTC

[Sanitized actual report](progress/postrelease-v0.1.0-verification-2026-10-01.md)
(дата имени — идентификатор поручения). Исторические значения таблиц выше сохранены.
Обычный uv tool v0.1.0→f7: 81 raw package/Git matches, 25 dependency pins,
idempotent install raw0. Один consolidated release на Windows/WSL: raw0/raw0,
1667 PASSED / 1663 PASSED + 4 Windows-only SKIPPED, coverage93%.
Plain installed CLI: три primary models, actual batch2/concurrency2 и deliberate
managed-ref retry — 6 completed Jobs, 5 paid CLI invocations, actual27.8 RUB.
Текущий lifetime known115.4 + historical UNKNOWN77 = conservative192.4/200 RUB;
новые wire POST counts не измерялись и не заявлены. Старые uncertain Jobs не тронуты.
Permanent workflow — [RELEASE.verified-tool](../../instructions/RELEASE.verified-tool.instructions.md).
Worker verification и independent evidence-only review завершены. Уточнение reviewer
о числе файлов отражено в отчёте: 11 outputs (6 final + 5 originals).
Docs commit/push интегратора — не новый релиз или перемещение v0.1.0.
