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
| Последний проверенный source SHA среза E06 | `2110a5fe5c4547a40f438f9e965ff15632a95e7c` (C09c2, **не** приёмка E06) |
| Незавершённое | Production model bindings, верхние цены и приёмка E06; интеграция скачивания в Job относится к E07. Статус worktree проверяется Git, а не этой статичной строкой |
| Обязательный объём и отложенное | [`release-scope.md`](release-scope.md) + change notes `CN-01`–`CN-03` |
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

## E06: Polza adapter и проверенные bindings — `IN_PROGRESS` 🔌

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

### Что ещё должно появиться до закрытия E06

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

## E07–E11: ещё не начато ⏳

| Этап | Что должно появиться | Зависимости | Критерий приёмки (из [`README.md`](README.md)) | Состояние |
|---|---|---|---|---|
| E07 | Одиночный Job end-to-end: входы → Job в SQLite → submit → polling → cost/usage → artifact → final state | E04–E06 | Синхронизированный Fake provider + реальная SQLite/файлы; ошибки сохраняют ref/cost и не создают скрытый повторный submit | `PENDING` |
| E08 | Batch с ограниченной конкурентностью, partial failures, `retry`/`sync`, Ctrl+C, recovery и защита от двойного Runner | E07 | Peak active = лимит, partial outcome, submit_count = 1 при неизвестном исходе, restart без нового POST, cross-process guard | `PENDING` |
| E09 | Полный CLI, JSON/exit codes, история, лексический FTS5, валютные сводки, atomic help | E03, E08 | Реальный argv, разбираемый JSON на успехе и ошибке, известный corpus находится поиском, RUB/USD/unknown раздельно, help из установленного пакета вне cwd | `PENDING` |
| E10 | Приёмка release candidate: Windows+Linux, offline quick/full/release, wheel+sdist, installed smoke, secret scan, агентский walkthrough, ограниченный live | E09 | Один frozen SHA; отсутствие обязательных `SKIP`/`XFAIL`/`NOT_RUN`; live-отчёт с model ID, режимом, ценой и датой | `PENDING` |
| E11 | Публикация проверенного релиза | E10 + отдельное разрешение владельца | Annotated tag на проверенном SHA, wheel/sdist + checksums, установка по тегу с проверкой происхождения | `PENDING`, `BLOCKED` (нет GitHub owner и разрешения) |

E07–E11 **не** содержат реализации, tests и evidence: файлов этих этапов в дереве
нет. Названия будущих тестов на доске не являются признаком их существования.

## Дополнительная работа по изменению объёма (CN) 🧩

Принятый объём расширен change notes в [`release-scope.md`](release-scope.md);
эти строки не являются реализованной функцией.

| ID | Работа | Зависимости | Что докажет приёмка | Состояние |
|---|---|---|---|---|
| CN-01 | Долговечные managed-копии reference images, связанные с Job (path/SHA-256/MIME/размер/позиция) в managed-дереве app data | E02 (inputs), E04 (storage v1) | Новая миграция **v2** без правки schema v1; no-clobber/ownership; Job→ref связь читается после удаления исходника; negative-тесты; managed-копии входят в quiesced backup-манифест. Контракт — [`07-storage-history-costs.md`](07-storage-history-costs.md), «Managed-копии reference images»; реализован первый code-срез: поле `InputRef.managed_path` и таблица v2 `managed_input_copies` со связью в repository. Файловый шов добавляет snapshots, `inputs/` no-clobber publish/resolve, confirmation hook и tmp manual backup/restore тест; полная E07/CLI/history-композиция не реализована | `IN_PROGRESS` (DTO/SQLite принят; файловый шов — writer candidate, independent review pending; весь `CN-01` не принят) |
| CN-02 | Лексический FTS5 и фильтры остаются обязательными; семантический/векторный поиск — backlog следующего релиза | E09 для FTS5 | FTS5/фильтры находят Jobs/prompts/ref-метаданные/artifacts; FTS5 не выдаётся за семантический поиск | `PENDING` (semantic — вне v0.1.0) |
| CN-03 | Audio/speech/STT/TTS — отдельное приложение, вне планов и навигации этого репозитория | — | В `docs/plans/` нет речи как задачи aimedia; отменяемые положения `01` (раздел «Расширение на аудио» и примеры `aimedia audio ...`) явно superseded с сохранением исходного текста; ссылки не битые | `DONE` (документарно) |

**Проверенный подсрез CN-01 — DTO/SQLite:** source SHA
`a237d4f7e485d37a92f1467a490b74413537dcb1`; production v1→v2, сохранность связей и
оба P1 проверены. Свежий native Sol 6.1 review — `PRECOMMIT: OK`; parent full и
full чистого клона — **1192 passed**, все 11 raw exit codes — 0 (Windows).
Evidence вне Git: `artifacts/quality/a237d4f7e485d37a92f1467a490b74413537dcb1/`
(`summary.json`, `clone-receipt.json`). Linux/release/live — `NOT_RUN`; это не
приёмка файловых managed-копий, всего CN-01 или E06/E07.

**Writer candidate — файловый шов CN-01 (поверх HEAD `0946685`, без commit):**
`snapshot_reference_images`, `LocalManagedInputStorage`, `archive_reference_images`;
DDL v1/v2 и repository не изменены. Добавлены **60** offline cases: реальные
FS+SQLite copies/reopen/resave, source delete/mutate, no-clobber, traversal и
symlink/junction (включая реальный Windows junction), partial publish/rollback/
possible commit/неверное подтверждение/tamper, реальный Polza MockHTTP и ручной
quiescent DB+inputs+outputs manifest/restore. После timeout-resume добавлены
strict positive Job ID cases и stat/ограниченное size+1 чтение oversized tampered copy.
Последний focused — **188 passed**, raw 0; свежий writer full — **1252 passed**,
все **11** raw exit codes 0 (Windows); предыдущий full 1248/raw 0 сохранён отдельно.
Evidence вне Git: `artifacts/quality/cn01-files-sol61-writer/` (summary/stdout/stderr),
`cn01-files-raw-codes.json` рядом; ранние failed attempts сохранены отдельно,
включая первый full с mypy raw 1 (pytest 1248 passed не маскирует failure).
Independent review, frozen SHA/clean-clone verification — **PENDING**; полный
E07/CLI/D03/history show/retry/sync и FTS — **NOT IMPLEMENTED**, Linux/release/live
и реальные пользовательские данные — **NOT_RUN**. Это не приёмка всего CN-01.

## Зависимости и блокеры 🚧

| ID | Блокер | Что блокирует | Что требуется, чтобы снять | Владелец |
|---|---|---|---|---|
| B1 | Гайды называют model IDs и режимы, но production bindings, живую доступность и **верхнюю** цену выбранного режима не подтверждают (каталог публикует только «от») | Закрытие E06, live-часть E07/E10, gate G3, любые заявления о проверенной поддержке модели | Сверить ID/режим/верхнюю цену до платного POST, затем ограниченный live | владелец + исполнитель |
| B2 | **RESOLVED для C09c1/C09c2**: `c794c0b` (1019) и `2110a5f` (1145) offline passed в чистых клонах, SOL6 предкоммитные reviews OK | — | E06 в целом остаётся открытым | исполнитель + интегратор |
| B3 | Есть clean-clone gate для C09c2, но нет frozen candidate всего E06 (bindings/ценовой preflight отсутствуют) | E06 → E07, ворота G2 | Зафиксировать законченный E06 source SHA и прогнать применимые offline-gates на нём | интегратор |
| B4 | Нет GitHub owner и явного разрешения на создание repo/push/tag/release | E11 | Точное имя owner и отдельное разрешение | владелец |
| B5 | Live-бюджет расходуется только по заранее согласованному плану (≤ 200 ₽ суммарно, без автоматического платного retry) | Генерация asset, E10 live | Согласованный список вызовов на каждую модель/режим | владелец |
| B6 | **RESOLVED (документарно)**: правка принятой спецификации под `CN-01` сделана — 07/03/README E04-E07/verification-matrix R07-R18/backup-contract и инструкции PROCESSING/CORE/DATA; контракт `e90e4c5`, DTO/SQLite-срез `a237d4f` | — | Файловый шов CN-01 — writer candidate; полная E07/CLI/D03/history-композиция и независимая приёмка ещё требуются | исполнитель |

`B4` (GitHub owner и разрешение на публикацию) — единственный блокер, который
останавливает зависимую работу целиком; остальные ограничивают конкретные этапы и
не отменяют офлайн-часть E06–E09.

## Критерии релиза (ворота G0–G7) 🚦

| Ворота | Критерий | Текущее состояние (не E06/release candidate) |
|---|---|---|
| G0 | Нет незакрытого решения, от которого зависит реализация | Частично: E00 baseline D01–D16 принят, `CN-01`–`CN-03` зафиксированы; контракт `CN-01` синхронизирован со спецификацией, реализован его первый code-срез (schema v2 + связь), файловый шов — отдельный writer candidate без принятой E07/CLI-композиции |
| G1 | Функции согласованного scope существуют и интегрированы | Не выполнено: E07–E09 не начаты |
| G2 | Обязательные offline-assertions выполнены на кандидате | C09c2: clean-clone full 1145 passed; CN-01 DTO/SQLite `a237d4f`: clean-clone full 1192 passed; E06/release candidate ещё нет |
| G3 | Есть live evidence заявленных моделей/режимов | Не выполнено: `NOT_RUN` |
| G4 | Собранные distributions и установленный пакет работают вне checkout | Не выполнено для текущего HEAD (приёмка E04/E05 не переносится на новые срезы) |
| G5 | Reviewer проверил evidence на одном SHA | Не выполнено для кандидата |
| G6 | Annotated tag указывает на проверенный SHA | Не выполнено |
| G7 | Личная копия установлена по тегу с проверяемым происхождением | Не выполнено |

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
| E06 | `null` для **всего этапа**; промежуточный C09c2: `2110a5fe5c4547a40f438f9e965ff15632a95e7c` | да для clone C09c2, этап не готов | C09c2: full `PASSED` (1145); release `NOT_RUN`; E06 final `NOT_RUN` | SOL6 `PRECOMMIT OK` для C09c2; финальный E06 review `NOT_RUN` | локально `artifacts/quality/2110a5fe5c4547a40f438f9e965ff15632a95e7c/summary.json`; commit `2110a5f` |
| E07–E11 | `null` | `null` | `NOT_RUN` | — | `null` |

Поля заполняются по [`progress/stage-report.template.json`](progress/stage-report.template.json);
строка R01–R24 в [`verification-matrix.md`](verification-matrix.md) получает реальный
pytest node ID и статус только после прогона на конкретном SHA.

## Границы достоверности 🔬

- Доска не объявляет E06–E11 реализованными и не заменяет приёмку: commit C09c2,
  clean-clone full gate и SOL6 предкоммитный review доказывают только gateway и
  безопасное скачивание по документированному URL, а не интеграцию Job, production
  bindings или release candidate.
- Production model IDs, цены и режимы не подтверждены; YAML-запись и fixture не
  доказывают живую поддержку.
- `SKIPPED`, `XFAILED`, `NOT_RUN`, `NOT_COLLECTED` не равны `PASSED`; строка без
  `PASSED` означает незакрытое требование.
- Статус `DONE` для `CN-03` относится только к документарному выводу speech из
  навигации и не означает изменений кода.

## Ссылки 🔗

- [`README.md`](README.md) — карта плана, этапы, коммиты и runbook релиза.
- [`release-scope.md`](release-scope.md) — обязательный объём, отложенное и change notes `CN-01`–`CN-03`.
- [`verification-matrix.md`](verification-matrix.md) — R01–R24, владелец этапа и слоты evidence.
- [`decisions/implementation-baseline.md`](decisions/implementation-baseline.md) — D01–D16.
- [`backup-contract.md`](backup-contract.md) — quiescent offline backup (managed files).
- [`logging-contract.md`](logging-contract.md) — диагностические события и redaction.
