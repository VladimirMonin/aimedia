# Implementation Baseline — решения развилок E00 ⚖️

> [!abstract] Назначение документа
> Этот документ закрывает 16 развилок E00: фиксирует исходное положение в
> спецификациях, принятое правило, затронутые документы и требуемую проверку.
> Пока правило относится к **планируемому** поведению: реализации, тестов и
> evidence ещё нет (E01+). Ссылки на «исходное положение» указывают файл:строку
> на момент baseline `cbee70d`; после точечных правок E00 смысл фрагментов
> сохранён, а правила дополнены ссылкой на этот baseline.

Область применения: `docs/plans/01`–`09`. Обязательный объём — в
[release-scope.md](../release-scope.md); начальные требования — в
[verification-matrix.md](../verification-matrix.md).

Обозначения: **ИП** — исходное положение, **Решение** — принятое правило,
**Документы** — затронутые файлы, **Проверка** — требуемый тест/доказательство.

---

## D01. Terminal states и recovery

- **ИП:** `03` Terminal states — terminal Job не возвращается в non-terminal
  (`03-domain-model.md` Terminal states, инв. 13); `08` вводит `failed → completed`
  для того же remote execution (`08-job-execution.md` Terminal states и recovery).
- **Решение:** terminal = `completed`/`failed`/`cancelled`. Единственное исключение —
  `failed → completed` при recovery **того же** remote execution без новой генерации.
  `sync` не вызывает submit, не стирает прежнюю ошибку, при продолжающемся remote Job
  обновляет только наблюдение; прежняя ошибка сохраняется как запись recovery.
- **Документы:** `03`, `08`.
- **Проверка:** R13 (recovery без нового submit), R23 (неполная финализация не маскирует
  платный failure); тест перехода состояний.

## D02. Тайм-аут и Ctrl+C

- **ИП:** `08` различает локальное прекращение ожидания и удалённое вычисление
  (`08-job-execution.md` Job timeout, Ctrl+C).
- **Решение:** локальное прекращение ожидания ≠ remote cancel. Known remote ref
  сохраняется; timeout с ref → recovery разрешён; неизвестный outcome submit →
  автоматический повтор запрещён. Ctrl+C не отправляет remote cancel.
- **Документы:** `08`, `04` (exit code `130`).
- **Проверка:** R12 (нет дубля при неизвестном outcome), R15 (Ctrl+C не выдан за
  remote cancel).

## D03. Момент создания Job

- **ИП:** `08` — схемы creation встречаются и до, и после validation
  (`08-job-execution.md` Job создаётся до provider submit).
- **Решение:** синтаксическая ошибка CLI (`Exit code 2`) Job не создаёт. Job
  создаётся после успешного разбора intent и чтения источников/входов, **до**
  предметной pre-submit validation; validation может завершить Job ошибкой.
  `provider.submit()` без сохранённого Job запрещён.
- **Документы:** `08`, `04`.
- **Проверка:** R02 (invalid request → `submit_count == 0`); интеграционный тест
  персистентности при сбое submit.

## D04. Порядок prompt sources

- **ИП:** `04` требует общий порядок перемежающихся `--prompt`/`--prompt-file`
  (`04-cli-contract.md` Порядок объединения, Разделитель).
- **Решение:** один упорядоченный список источников `--prompt`/`--prompt-file`;
  разделитель `\n\n`; два отдельных списка с последующей склейкой — **не**
  реализация контракта.
- **Документы:** `04`.
- **Проверка:** R01 (compiled prompt после реального argv и чтения БД).

## D05. Область глобальных опций

- **ИП:** `04` описывает global options, но примеры ставят их и после подкоманды
  (`04-cli-contract.md` Общие глобальные опции).
- **Решение:** `--json` и `--provider` валидны **до домена** и у конечной команды.
  Разные значения при повторе — ошибка (`Exit code 2`); одинаковые повторные
  значения допустимы.
- **Документы:** `04`.
- **Проверка:** R16 (JSON contract) и subprocess-тест реального argv; R01
  (argv-order).

## D06. Model ID и binding

- **ИП:** примеры используют точки/дефисы и варианты `remote_id`/`remote_model_id`
  (`03`, `05`, `06`, `01`).
- **Решение:** канонические имена — `model_id` (canonical ID логической модели) и
  `remote_model_id` (фактический ID у provider). Alias → canonical; демонстрационные
  ID не объявляются проверенными.
- **Документы:** `01`, `03`, `05`, `06`, `07`.
- **Проверка:** R03 (однозначное разрешение alias/effective override + snapshot
  resolved IDs в истории).

## D07. Mapping в YAML

- **ИП:** `05` закрепляет mapping за adapter; `06` допускает `parameter_map`
  (`06-model-registry.md` Допустимый declarative provider mapping).
- **Решение:** в v0.1 mapping из domain-полей в provider-параметры выполняет
  **Python adapter**. YAML хранит bindings и ограничения; `parameter_map` —
  неактивная будущая возможность. Один механизм, не два конкурирующих.
- **Документы:** `05`, `06`.
- **Проверка:** контрактный тест Polza request (точный HTTP payload), R03.

## D08. Формат файла (`final_format` vs provider output)

- **ИП:** `04` задаёт `--format` как локальный конечный формат; `06` обсуждает
  provider output отдельно (`06-model-registry.md` Output format: provider vs local).
- **Решение:** CLI `final_format` (png/jpeg/webp; alias `jpg`) отделён от provider
  output param. Локальный WebP не означает native WebP модели.
- **Документы:** `04`, `06`.
- **Проверка:** R05 (декодирование, dimensions, MIME, size, SHA-256).

## D09. `--out` и копии

- **ИП:** `07` — пользовательский путь заменяет стандартный путь результата
  (`07-storage-history-costs.md` Пользовательский `--out`).
- **Решение:** один конечный файл по фактическому пути (managed или user_output);
  скрытого двойного копирования нет. `--keep-original` сохраняет оригинал отдельной
  ролью artifact.
- **Документы:** `04`, `07`.
- **Проверка:** R06 (outputs не перезаписываются при параллельном сохранении),
  R05.

## D10. Удалённая операция

- **ИП:** `05` допускает разные remote endpoints, ранняя схема `07`/`03` хранит в
  основном ID (`03` RemoteJobRef, `05` RemoteJobRef расширенного вида).
- **Решение:** каноническое имя поля remote job — `remote_job_id` (не `remote_id`);
  тип операции хранится в remote ref/snapshot (image = `media`); endpoint по строке
  ID не угадывается.
- **Документы:** `03`, `05`, `07`, `01`.
- **Проверка:** контрактный тест remote ref/operation; R07 (история переживает
  перезапуск с сохранённым operation).

## D11. Деньги и миграции

- **ИП:** `07` рекомендует decimal TEXT и допускает разные migration runners
  (`07-storage-history-costs.md` Тип `cost_amount`, Peewee migrator).
- **Решение:** decimal-as-TEXT ↔ `Decimal` без float; суммы по валюте считаются в
  Python, без SQL `SUM()`; **один** migration runner (конкретный API — после проверки
  версии Peewee на E01/E04). Ноль ≠ unknown.
- **Документы:** `07`.
- **Проверка:** R08 (RUB/USD/unknown/zero не смешиваются), R18 (миграции сохраняют
  историю).

## D12. `--raw` / атомарная справка

- **ИП:** `09` описывает raw source, затем выбирает resolved raw
  (`09-documentation-help.md` Raw mode и directives).
- **Решение:** `--raw` возвращает resolved Markdown без front matter и
  неразрешённых директив; ANSI нет. Source остаётся читаемым MD (отдельная роль).
- **Документы:** `09`.
- **Проверка:** R17 (resolved raw/JSON equivalence + installed package вне cwd).

## D13. Exit codes

- **ИП:** `04` не закрывает все случаи interrupt/internal error
  (`04-cli-contract.md` Exit codes).
- **Решение:** сохранить `0` и `2`–`9`; добавить `130` (штатно обработанный Ctrl+C)
  и `1` (внутренняя непредвиденная ошибка).
- **Документы:** `04`.
- **Проверка:** R16 (parse stdout + exit code + отсутствие ANSI).

## D14. Локальная принадлежность Job (local ownership)

- **ИП:** `08` — v0.1 не ориентирована на несколько процессов над одним Job
  (`08-job-execution.md` Multi-process race).
- **Решение:** минимальный cross-process guard (status-guard + короткая транзакция);
  без брокера и распределённой блокировки. Конкретная схема и снятие stale
  ownership **утверждаются до E08** (здесь — требование, не реализация).
- **Документы:** `08`.
- **Проверка:** R24 (два локальных процесса не исполняют один Job одновременно).

## D15. Границы scope

- **ИП:** `01` — FTS5 и статистика расходов «желательно», `--detach`/удаление/манифесты
  — желательно (`01-product-scope.md`).
- **Решение:** FTS5 и сводка расходов по валютам — **обязательные** (E09);
  `--detach`, удаление истории, универсальные batch manifests, публичные
  maintenance-команды — не реализуются в v0.1.
- **Документы:** `01`, [release-scope.md](../release-scope.md), `README`.
- **Проверка:** R20 (поиск и расходы проверяют полезный результат); сверка scope.

## D16. Доказательство поддержки модели

- **ИП:** `01`/`06` содержат демонстрационные ID без указания проверки.
- **Решение:** YAML-запись ≠ проверенная поддержка. Для модели фиксируются remote ID,
  источник параметров, дата и результаты live-сценариев; `4K` не помечается
  испытанным при проверке только `1K`.
- **Документы:** `01`, `06`, [release-scope.md](../release-scope.md).
- **Проверка:** R04 (live evidence по отдельным режимам).

---

## Итоговая таблица развилок

| # | Развилка | Решение | Документы | Требуемый тест |
|---|---|---|---|---|
| D01 | Terminal/recovery | `failed → completed` только при recovery того же remote execution | 03, 08 | R13, R23 |
| D02 | Timeout/Ctrl+C | Локальное ожидание ≠ remote cancel; known ref сохраняется | 08, 04 | R12, R15 |
| D03 | Момент создания Job | Синтаксическая ошибка не создаёт Job; submit без Job запрещён | 08, 04 | R02 |
| D04 | Порядок prompt sources | Один список; разделитель `\n\n`; без склейки двух списков | 04 | R01 |
| D05 | Global options | `--json`/`--provider` до домена и у команды; конфликт = ошибка | 04 | R16 |
| D06 | Model ID/binding | `model_id` / `remote_model_id`; alias → canonical | 01, 03, 05, 06, 07 | R03 |
| D07 | YAML mapping | Mapping в Python adapter; `parameter_map` неактивен | 05, 06 | contract |
| D08 | Final format | `final_format` отделён от provider output | 04, 06 | R05 |
| D09 | `--out` | Один конечный файл; `--keep-original` отдельно | 04, 07 | R06 |
| D10 | Remote operation | `remote_job_id` + `operation`; endpoint не угадывается | 03, 05, 07 | R07 |
| D11 | Money/migrations | decimal TEXT ↔ Decimal; суммы в Python; один runner | 07 | R08, R18 |
| D12 | Raw help | Resolved MD без front matter/директив; ANSI нет | 09 | R17 |
| D13 | Exit codes | Сохранить 0/2–9; добавить 130 и 1 | 04 | R16 |
| D14 | Local ownership | Минимальный guard; схема и stale — до E08 | 08 | R24 |
| D15 | Scope | FTS5 + сводка расходов обязательны; ряд функций не в v0.1 | 01, README | R20 |
| D16 | Verified model evidence | YAML ≠ проверенная поддержка; фиксировать remote ID/источник/дату | 01, 06 | R04 |

Все 16 развилок имеют выбранный вариант; открытых блокеров уровня E00 нет.
Конкретные API (migration runner) и схема cross-process ownership уточняются на
своих этапах и не блокируют E00.
