# Verification Matrix — начальные требования aimedia v0.1.0 🧾

> [!abstract] Назначение документа
> Начальная матрица обязательных утверждений R01–R24 для первого релиза.
> Источник строк — сквозная матрица из [README](README.md). Этот документ задаёт
> **требования и минимальное доказательство**, а не результаты: `test_node_id` и
> `evidence_path` пока `null`, `status = NOT_RUN`. Реальные pytest node IDs,
> состояния прогона и ссылки на evidence заполняются по мере реализации (E01+).
> `NOT_RUN`/`SKIPPED`/`XFAILED`/`NOT_COLLECTED` **не равны** `PASSED`.

Область применения: `docs/plans/01`–`09`. Обязательный объём и отложенное —
[release-scope.md](release-scope.md); решения развилок —
[decisions/implementation-baseline.md](decisions/implementation-baseline.md).

## Матрица R01–R24

| ID | Требование (утверждение) | Минимальное доказательство | Владелец этапа | test_node_id | status | evidence_path |
|---|---|---|---|---|---|---|
| R01 | Inline/file prompts не меняют порядок и текст | Сравнение compiled prompt после реального CLI argv и чтения БД | E02/E09 | null | NOT_RUN | null |
| R02 | Неподдерживаемые параметры не уходят в API | Invalid request + assertion `submit_count == 0` | E03/E07 | null | NOT_RUN | null |
| R03 | Model/provider IDs разрешаются однозначно | Alias/effective override tests + snapshot resolved IDs в истории | E03/E04 | null | NOT_RUN | null |
| R04 | Генерация и references работают через Polza | HTTP contracts + live evidence по отдельным режимам | E06/E10 | null | NOT_RUN | null |
| R05 | Локальный файл соответствует объявленному формату | Decode, dimensions, MIME, byte size и SHA-256 | E05/E07 | null | NOT_RUN | null |
| R06 | Outputs не перезаписываются при параллельном сохранении | Два simultaneous writers + проверка обоих исходных результатов | E05 | null | NOT_RUN | null |
| R07 | История переживает перезапуск | Новое соединение/процесс читает сохранённые prompts, inputs (path + SHA-256 и, после `CN-01`, managed-копию входа), refs (`remote_job_id` + `operation`), cost и artifacts | E04/E08 | null | NOT_RUN | null |
| R08 | RUB/USD и unknown/zero не смешиваются | Точные Decimal fixtures + currency grouping + unknown count | E04/E09 | null | NOT_RUN | null |
| R09 | Поля `cost` и `cost_rub` не удваивают расход | Один billing snapshot с обоими aliases + повторный sync | E06/E08 | null | NOT_RUN | null |
| R10 | Batch действительно параллелен и ограничен | Контролируемое перекрытие tasks, peak active, submit count и release slots | E08 | null | NOT_RUN | null |
| R11 | Один Job failure не убивает весь batch | 5 Jobs, 1 failure, 4 сохранённых успеха | E08 | null | NOT_RUN | null |
| R12 | Неизвестный outcome submit не создаёт дубль | Provider принимает POST и рвёт соединение; повторного POST нет | E08 | null | NOT_RUN | null |
| R13 | Recovery не равен повторной генерации | Restart + sync + assertion нулевых новых submit | E08 | null | NOT_RUN | null |
| R14 | Retry создаёт новый Job и сохраняет lineage | Два разных локальных IDs/remote refs; история исходного неизменна | E08 | null | NOT_RUN | null |
| R15 | Ctrl+C не выдаётся за подтверждённую remote cancellation | Process interruption + known ref в БД + отсутствие cancel request | E08 | null | NOT_RUN | null |
| R16 | JSON чистый и ошибки машинно различимы | Parse всего stdout, exit code, schema и отсутствие ANSI для success/error | E09 | null | NOT_RUN | null |
| R17 | Справка автономна и соответствует capabilities | Resolved raw/JSON equivalence + installed package outside cwd | E03/E09/E10 | null | NOT_RUN | null |
| R18 | Миграции сохраняют историю | Upgrade fixture, failure rollback, repeated apply и FK checks; для `CN-01` — v1 → v2 без правки v1 DDL, сохранение данных v1, no-op повторного apply и FK/`ON DELETE CASCADE` таблицы `managed_input_copies` | E04 | null | NOT_RUN | null |
| R19 | Ключ не утёк в продуктовые данные и логи | Canary в request/error → скан stdout/stderr/logs/БД/distributions | E01/E06/E10 | null | NOT_RUN | null |
| R20 | Local search и расходы проверяют полезный результат | Известный corpus → ожидаемые IDs из `jobs search` и точные суммы периода из `jobs costs` (по валютам) | E09 | null | NOT_RUN | null |
| R21 | Installed tool соответствует релизу | Git peeled SHA ↔ installed VCS commit; версия и package resources | E11 | null | NOT_RUN | null |
| R22 | Отсутствие ключа не ломает локальные команды | Subprocess help/models/history без ключа и без внешней сети | E01/E09 | null | NOT_RUN | null |
| R23 | Неполная финализация не маскирует платный failure | Remote success + local error → saved actual cost, error и recoverable ref | E07/E08 | null | NOT_RUN | null |
| R24 | Два локальных процесса не исполняют один Job одновременно | Cross-process contention test с одним фактическим submit | E08 | null | NOT_RUN | null |

## Уточнение `CN-01` (контракт синхронизирован, реализация отсутствует) 🗂️

[`release-scope.md`](release-scope.md) `CN-01` расширяет два требования матрицы:
байты копий входов долговечны. Контракт зафиксирован в
[`07-storage-history-costs.md`](07-storage-history-costs.md), раздел «Managed-копии
reference images».

- **R07** — история переживает перезапуск вместе со связью «вход → managed-копия»,
  а не только с `path`/`sha256` исходника; копия читается после удаления исходника.
- **R18** — миграция v1 → v2 добавляет только `managed_input_copies`: DDL v1 не
  переписывается, данные v1 сохраняются, повторный apply — no-op, FK и
  `ON DELETE CASCADE` работают.

Состояние обеих строк остаётся `NOT_RUN`: миграции v2, таблицы и поля
`InputRef.managed_path` в дереве нет. `test_node_id` и `evidence_path` заполняются
только после реального прогона на конкретном SHA — ожидаемые node IDs сюда не
вписываются.

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
