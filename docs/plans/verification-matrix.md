# Verification Matrix — начальные требования aimedia v0.1.0 🧾

> [!abstract] Назначение документа
> Начальная матрица обязательных утверждений R01–R24 для первого релиза.
> Источник строк — сквозная матрица из [README](README.md). Этот документ задаёт
> **требования и минимальное доказательство**, с накопленной offline writer
> evidence в таблице. `PASSED (offline writer; review pending)` не означает
> независимую приёмку или live-поддержку; R04/R21 сохраняют отдельные ограничения.
> `NOT_RUN`/`SKIPPED`/`XFAILED`/`NOT_COLLECTED` **не равны** `PASSED`.

Область применения: `docs/plans/01`–`09`. Обязательный объём и отложенное —
[release-scope.md](release-scope.md); решения развилок —
[decisions/implementation-baseline.md](decisions/implementation-baseline.md).

## Матрица R01–R24

| ID | Требование (утверждение) | Минимальное доказательство | Владелец этапа | test_node_id | status | evidence_path |
|---|---|---|---|---|---|---|
| R01 | Inline/file prompts не меняют порядок и текст | Сравнение compiled prompt после реального CLI argv и чтения БД | E02/E09 | `tests/cli/test_subprocess_generation.py::test_subprocess_generate_then_restart_reads_managed_copy_and_search` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R02 | Неподдерживаемые параметры не уходят в API | Invalid request + assertion `submit_count == 0` | E03/E07 | `tests/cli/test_complete_cli.py::test_semantic_and_output_errors_are_failed_jobs_without_post` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R03 | Model/provider IDs разрешаются однозначно | Alias/effective override tests + snapshot resolved IDs в истории | E03/E04 | `tests/unit/test_model_resolution.py; tests/integration/test_single_image.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R04 | Генерация и references работают через Polza | HTTP contracts + live evidence по отдельным режимам | E06/E10 | null | NOT_RUN | null |
| R05 | Локальный файл соответствует объявленному формату | Decode, dimensions, MIME, byte size и SHA-256 | E05/E07 | `tests/cli/test_complete_cli.py::test_generate_restart_show_search_costs_copies_retry_and_clean_billing` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R06 | Outputs не перезаписываются при параллельном сохранении | Два simultaneous writers + проверка обоих исходных результатов | E05 | `tests/integration/test_output_collisions.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R07 | История переживает перезапуск | Новое соединение/процесс читает сохранённые prompts, inputs (path + SHA-256 и, после `CN-01`, managed-копию входа), refs (`remote_job_id` + `operation`), cost и artifacts | E04/E08 | `tests/cli/test_subprocess_generation.py; tests/cli/test_complete_cli.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R08 | RUB/USD и unknown/zero не смешиваются | Точные Decimal fixtures + currency grouping + unknown count | E04/E09 | `tests/integration/test_cost_aggregation.py; tests/integration/test_cost_storage.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R09 | Поля `cost` и `cost_rub` не удваивают расход | Один billing snapshot с обоими aliases + повторный sync | E06/E08 | `tests/contracts/test_polza_costs.py; tests/cli/test_complete_cli.py::test_sync_reuses_real_persisted_partial_files_without_duplicate_cost_or_output` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R10 | Batch действительно параллелен и ограничен | Контролируемое перекрытие tasks, peak active, submit count и release slots | E08 | `tests/cli/test_complete_cli.py::test_batch_five_jobs_overlap_bounded_whole_flows_and_expected_failure` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R11 | Один Job failure не убивает весь batch | 5 Jobs, 1 failure, 4 сохранённых успеха | E08 | `tests/cli/test_complete_cli.py::test_batch_five_jobs_overlap_bounded_whole_flows_and_expected_failure` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R12 | Неизвестный outcome submit не создаёт дубль | Provider принимает POST и рвёт соединение; повторного POST нет | E08 | `tests/cli/test_complete_cli.py::test_no_key_and_unknown_submit_are_not_automatically_retried` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R13 | Recovery не равен повторной генерации | Restart + sync + assertion нулевых новых submit | E08 | `tests/cli/test_subprocess_generation.py::test_subprocess_sigint_retains_ref_then_explicit_sync_has_no_post` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R14 | Retry создаёт новый Job и сохраняет lineage | Два разных локальных IDs/remote refs; история исходного неизменна | E08 | `tests/cli/test_complete_cli.py::test_generate_restart_show_search_costs_copies_retry_and_clean_billing` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R15 | Ctrl+C не выдаётся за подтверждённую remote cancellation | Process interruption + known ref в БД + отсутствие cancel request | E08 | `tests/cli/test_subprocess_generation.py::test_subprocess_sigint_retains_ref_then_explicit_sync_has_no_post` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R16 | JSON чистый и ошибки машинно различимы | Parse всего stdout, exit code, schema и отсутствие ANSI для success/error | E09 | `tests/cli/test_complete_cli.py::test_whole_stdout_json_for_local_and_argv_errors; tests/cli/test_subprocess_generation.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R17 | Справка автономна и соответствует capabilities | Resolved raw/JSON equivalence + installed package outside cwd | E03/E09/E10 | `tests/cli/test_complete_cli.py::test_source_error_no_job_and_help_raw_json_equivalence; installed_smoke.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R18 | Миграции сохраняют историю | Upgrade fixture, failure rollback, repeated apply и FK checks; для `CN-01` — v1 → v2 без правки v1 DDL, сохранение данных v1, no-op повторного apply и FK/`ON DELETE CASCADE` таблицы `managed_input_copies` | E04 | `tests/integration/test_history_search.py; tests/integration/test_migration_failure.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R19 | Ключ не утёк в продуктовые данные и логи | Canary в request/error → скан stdout/stderr/logs/БД/distributions | E01/E06/E10 | `tests/security/test_download_auth.py; tests/cli/test_complete_cli.py; installed_smoke.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R20 | Local search и расходы проверяют полезный результат | Corpus prompts, reference source/managed paths/hashes и result artifact filenames/paths/hashes → ожидаемые Job IDs после restart без исходника; DB-only legacy backfill/resave; точные суммы периода по валютам | E09 | `tests/cli/test_subprocess_generation.py::test_subprocess_search_reference_and_result_snapshots_after_source_deleted; tests/integration/test_history_search.py; tests/cli/test_complete_cli.py::test_generate_restart_show_search_costs_copies_retry_and_clean_billing` | PASSED (offline writer; fresh review pending) | `artifacts/quality/whole-cli-consolidated-fixes/release/` |
| R21 | Installed tool соответствует релизу | Git peeled SHA ↔ installed VCS commit; версия и package resources | E11 | null | NOT_RUN | null |
| R22 | Отсутствие ключа не ломает локальные команды | Subprocess help/models/history без ключа и без внешней сети | E01/E09 | `tests/cli/test_complete_cli.py::test_whole_stdout_json_for_local_and_argv_errors; installed_smoke.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R23 | Неполная финализация не маскирует платный failure | Remote success + local error → saved actual cost, error и recoverable ref | E07/E08 | `tests/cli/test_complete_cli.py::test_sync_reuses_real_persisted_partial_files_without_duplicate_cost_or_output; tests/integration/test_single_image.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |
| R24 | Два локальных процесса не исполняют один Job одновременно | Cross-process contention test с одним фактическим submit | E08 | `tests/cli/test_subprocess_generation.py::test_subprocess_sigint_retains_ref_then_explicit_sync_has_no_post; tests/integration/test_execution_ownership.py` | PASSED (offline writer; review pending) | `artifacts/quality/complete-cli-sol61-writer/full-accepted-candidate/` |

## Уточнение `CN-01` (schema/связь и отдельный файловый шов) 🗂️

[`release-scope.md`](release-scope.md) `CN-01` расширяет два требования матрицы:
байты копий входов долговечны. Контракт зафиксирован в
[`07-storage-history-costs.md`](07-storage-history-costs.md), раздел «Managed-копии
reference images».

- **R07** — история переживает перезапуск вместе со связью «вход → managed-копия»,
  а не только с `path`/`sha256` исходника; копия читается после удаления исходника.
- **R18** — миграция v1 → v2 добавляет только `managed_input_copies`: DDL v1 не
  переписывается, данные v1 сохраняются, повторный apply — no-op, FK и
  `ON DELETE CASCADE` работают.

Первый code-срез `CN-01` дал поле `InputRef.managed_path` и таблицу v2 (миграция
v1 → v2, no-op повторного apply, FK/cascade/UNIQUE и round trip связи покрыты
focused offline-тестами). Файловый шов добавляет реальные copies/resolve и
confirmation hook; `tests/integration/test_managed_inputs.py` проверяет удаление
исходника, reopen/resave, no-clobber, preflight и отказы FS/DB, MockHTTP Polza.
`tests/integration/test_manual_managed_backup.py` — тест ручного quiescent manifest/restore
inputs+outputs+DB, не runtime backup. Полная E07/CLI/D03/history-композиция и live
**не реализованы/NOT_RUN**. Строки R07/R18 сохраняют release-статус `NOT_RUN` до
зафиксированного SHA и независимой приёмки; writer evidence — на release-board.
`CN-01`/`E06`/`E07` не считаются принятыми.

## Правила ведения матрицы

- `test_node_id` заполняется только фактическим pytest node ID из реального прогона;
  синтетические и «ожидаемые» node IDs не вписываются.
- `status` принимает значения `NOT_RUN`, `PASSED`, `FAILED`, `SKIPPED`, `XFAILED`,
  `NOT_COLLECTED`; `PASSED` означает конкретную команду на определённом SHA с
  реально выполненными assertions.
- `evidence_path` указывает на сохранённый результат прогона (JUnit/coverage/report
  того же SHA), а не на файл теста.
- Для обязательного требования отсутствие `PASSED` означает незакрытую проверку.
- Если требование исключено из scope, это отражается в
  [release-scope.md](release-scope.md), а не в skip-декораторе.
- Строки начальной матрицы наследуют владельца этапа из [README](README.md); при
  изменении плана этапов владелец обновляется здесь и в README.

## Writer follow-up: единый E07–E09 candidate, 2026-09-30

Записи выше о прежнем состоянии CN-01/E06 сохранены как исторические ограничения,
а не как описание текущего candidate. Теперь реализована публичная композиция CLI,
SQLite, Polza HTTP adapter, DNS-pinned downloader и verified outputs. Итоговый locked
Windows offline `full`: **1344 passed**, branch coverage **93%**, все collection,
Ruff lint/format и mypy raw exits **0**. Ни skips, ни xfails не добавлялись.

`full/` и `full-final/` сохранены с raw exit **1**: сначала устаревший oracle миграции
v2 вместо v2+v3, затем ожидание default-only sources после добавления изолирующего
config locator. Их не переименовывали в успешные проверки. Исправленный единый
прогон — `full-accepted-candidate/`. Source/tests после него заморожены для review.

Installed-wheel/outside-cwd smoke: `installed-smoke-03.txt` raw exit **0**; 10 тем
help, raw/JSON equality, официальный experimental catalog/pricing, local history,
search/costs/config и argv error JSON. В smoke используются явные disposable
`--config`, `--file`, `--data-dir`, socket guard и очищенное credential environment.
Первый ошибочно неизолированный default config init и санкционированное удаление
точно созданного template отражены отдельно в приватной writer evidence; это не
скрытый successful isolation claim для первых попыток.

R04 остаётся **NOT_RUN для live**; R21 — **NOT_RUN для VCS release install**.
Официальные bindings экспериментальны и live-unverified. Все остальные PASSED в
таблице ограничены offline writer evidence: независимый whole-bundle review,
freeze/commit, Linux, live E10 и E11 здесь не приняты и не имитируются. R17/R19
дополнительно используют `installed-smoke-03.txt`, не только pytest output.


## Консолидированная коррекция E07–E09 (кандидат, не приёмка)

Дополнительные assertions R02/R15/R16/R18/R20/R23: текущие sync 401/remote/local
failures и old FAILED running observation; parser invalid format без Job и valid jpg;
disposable default TOML/alias isolation; DB leaf redirect/typed backend failures;
DB-only refs/results FTS backfill/resave/restart; настоящий batch SIGINT с queued
CREATED, active ref/cost/copies, kernel ownership и GET-only recovery.
Новые узлы — `tests/cli/test_history_faults.py`, `tests/integration/test_database_leaf.py`
и source-named cases в `test_complete_cli.py`, `test_subprocess_generation.py`,
`test_history_search.py`, `test_settings.py`.
Writer focused: **69 passed**. Финальный offline release: **1366 passed / 93%**,
все **12 raw codes 0**, без SKIP/XFAIL;
`artifacts/quality/whole-cli-consolidated-fixes/release/summary.json`.
Первый release (**1365 passed / 1 failed**, raw 1) сохранён в `release-first-failed/`:
старый directory-leaf test ожидал raw PeeweeError вместо обязательного StorageError;
обновлена только эта assertion. Это не fresh review, clean clone, Linux/live или
принятие E07–E11. `--max-images > 1` остаётся fail-closed; multi-output live NOTPROVEN.
