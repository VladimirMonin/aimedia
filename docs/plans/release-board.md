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
| Последний проверенный source SHA среза E06 | `c794c0bcf0356b88e1388202b9717016b2d7ad95` (C09c1, **не** приёмка E06) |
| Незавершённое | C09c2 (CDN), подтверждённые model bindings и приёмка E06; статус worktree проверяется Git, а не этой статичной строкой |
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

Committed contract-тесты: `tests/contracts/test_polza_requests.py`,
`test_polza_responses.py`, `test_polza_costs.py`, `test_polza_gateway.py`.
Summary clean-clone gate хранится локально вне Git в
`artifacts/quality/c794c0bcf0356b88e1388202b9717016b2d7ad95/summary.json`;
воспроизводимый источник — commit SHA и команда.

### Что ещё должно появиться до закрытия E06

- C09c2: безопасное получение remote artifact/CDN (bearer не уходит на чужой host,
  обрабатывается redirect/SSRF), затем отдельный срез скачивания и декодирования.
- `tests/contracts/test_polza_errors.py` и `tests/security/test_download_auth.py` —
  сейчас этих файлов **нет** (приведены в плане E06, не реализованы).
- `tests/live/test_polza_smoke.py` и `tests/live/` — каталога нет; live-прогон не
  выполнялся, ключ не читался.
- Production bindings и верхние цены не подтверждены: встроенный каталог остаётся
  пустым (ограничение E03). Публичная документация указывает кандидатов
  `qwen/image-2.1` и `google/gemini-3.1-flash-image-preview`, но не доказывает
  доступность конкретных режимов или стоимость платного вызова.
- Приёмка wheel/sdist и installed-CLI smoke для E06 **не** выполнялась — это слот E10.

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
| CN-01 | Долговечные managed-копии reference images, связанные с Job (path/SHA-256/MIME/размер/позиция) в managed-дереве app data | E02 (inputs), E04 (storage v1) | Новая миграция **v2** без правки schema v1; no-clobber/ownership; Job→ref связь читается после удаления исходника; negative-тесты; managed-копии входят в quiesced backup-манифест. Целевой инвариант уже зафиксирован в [`PROCESSING.image-artifacts`](../../instructions/PROCESSING.image-artifacts.instructions.md) со статусом NOT IMPLEMENTED | `PENDING` |
| CN-02 | Лексический FTS5 и фильтры остаются обязательными; семантический/векторный поиск — backlog следующего релиза | E09 для FTS5 | FTS5/фильтры находят Jobs/prompts/ref-метаданные/artifacts; FTS5 не выдаётся за семантический поиск | `PENDING` (semantic — вне v0.1.0) |
| CN-03 | Audio/speech/STT/TTS — отдельное приложение, вне планов и навигации этого репозитория | — | В `docs/plans/` нет речи как задачи aimedia; отменяемые положения `01` (раздел «Расширение на аудио» и примеры `aimedia audio ...`) явно superseded с сохранением исходного текста; ссылки не битые | `DONE` (документарно) |

## Зависимости и блокеры 🚧

| ID | Блокер | Что блокирует | Что требуется, чтобы снять | Владелец |
|---|---|---|---|---|
| B1 | Авторитетные Polza model IDs, поддержка режимов/соотношений и верхняя цена не подтверждены | Закрытие E06, live-часть E07/E10, gate G3, любые заявления о поддержке модели | Официальный ID + подтверждённый режим + оценка цены до вызова, затем ограниченный live | владелец + исполнитель |
| B2 | **RESOLVED для C09c1**: `c794c0b`, 1019 offline passed в чистом clone, SOL6 предкоммитный review OK | — | C09c2 и E06 в целом остаются открытыми | исполнитель + интегратор |
| B3 | Есть clean-clone gate для C09c1, но ещё нет полного frozen candidate для E06 (CDN и bindings отсутствуют) | E06 → E07, ворота G2 | Зафиксировать законченный E06 source SHA и прогнать применимые offline-gates на нём | интегратор |
| B4 | Нет GitHub owner и явного разрешения на создание repo/push/tag/release | E11 | Точное имя owner и отдельное разрешение | владелец |
| B5 | Live-бюджет расходуется только по заранее согласованному плану (≤ 200 ₽ суммарно, без автоматического платного retry) | Генерация asset, E10 live | Согласованный список вызовов на каждую модель/режим | владелец |
| B6 | Правка принятой спецификации под `CN-01` не сделана (07/03/backup-contract/матрица); инструкция владельца уже обновлена | Закрытие CN-01 без расхождения с спецификацией | Отдельная задача обновления `07-storage-history-costs.md` и связанных документов | исполнитель |

`B4` (GitHub owner и разрешение на публикацию) — единственный блокер, который
останавливает зависимую работу целиком; остальные ограничивают конкретные этапы и
не отменяют офлайн-часть E06–E09.

## Критерии релиза (ворота G0–G7) 🚦

| Ворота | Критерий | Состояние после проверенного среза `c794c0b` (не E06 final) |
|---|---|---|
| G0 | Нет незакрытого решения, от которого зависит реализация | Частично: E00 baseline D01–D16 принят, `CN-01`–`CN-03` зафиксированы; `CN-01` требует будущей задачи по спецификации |
| G1 | Функции согласованного scope существуют и интегрированы | Не выполнено: E07–E09 не начаты |
| G2 | Обязательные offline-assertions выполнены на кандидате | C09c1: clean-clone full 1019 passed; E06/release candidate ещё нет |
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
| E06 | `null` для **всего этапа**; промежуточный C09c1: `c794c0bcf0356b88e1388202b9717016b2d7ad95` | да для clone C09c1, этап не готов | C09c1: full `PASSED` (1019); release `NOT_RUN`; E06 final `NOT_RUN` | SOL6 `PRECOMMIT OK` для C09c1; финальный E06 review `NOT_RUN` | локально `artifacts/quality/c794c0bcf0356b88e1388202b9717016b2d7ad95/summary.json`; commit `c794c0b` |
| E07–E11 | `null` | `null` | `NOT_RUN` | — | `null` |

Поля заполняются по [`progress/stage-report.template.json`](progress/stage-report.template.json);
строка R01–R24 в [`verification-matrix.md`](verification-matrix.md) получает реальный
pytest node ID и статус только после прогона на конкретном SHA.

## Границы достоверности 🔬

- Доска не объявляет E06–E11 реализованными и не заменяет приёмку: commit C09c1,
  clean-clone full gate и SOL6 предкоммитный review доказывают только шлюз без CDN,
  а не завершение Polza adapter, production bindings или release candidate.
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
