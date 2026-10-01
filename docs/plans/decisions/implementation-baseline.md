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
  ролью artifact. Уточнение `CN-01`: правило относится к результату Job — managed-копия
  входного reference image не является artifact результата и не создаёт второго
  копирования output (`07-storage-history-costs.md`, «Managed-копии reference
  images»); `--out` остаётся пользовательским выводом результата.
- **Документы:** `04`, `07`.
- **Проверка:** R06 (outputs не перезаписываются при параллельном сохранении),
  R05.

## D10. Удалённая операция

- **ИП:** `05` допускает разные remote endpoints, ранняя схема `07`/`03` хранит в
  основном ID (`03` RemoteJobRef, `05` RemoteJobRef расширенного вида).
- **Решение:** каноническое имя поля remote job — `remote_job_id` (не `remote_id`);
  тип операции `operation` (для image — `media`) хранится в remote ref/provider
  snapshot (`jobs`); endpoint по строке ID не угадывается. Когда provider вернул
  операцию, она сохраняется вместе с `remote_job_id`, а не выводится заново при
  recovery/`sync`.
- **Документы:** `03`, `05`, `07`, `01`.
- **Проверка:** контрактный тест remote ref/operation; R07 (история переживает
  перезапуск с сохранёнными prompts, refs — включая `remote_job_id` и `operation` —,
  cost и artifacts).

### Принятое уточнение D10: documented async Media submit

По принятому dialogue `16f9` на source `e751f97633eea5c33b8831186dad98d0af5f422b`
для exact `qwen/image-2.1`, `google/gemini-3.1-flash-image-preview` и
`openai/gpt-5.4-image-2@mie` в существующем fixed-provider условии добавляется
только top-level boolean `async: true` до окончательного serialized body cap.
only=[mie]/allow_fallbacks=false/Decimal-ceil RUB, count и references неизменны;
caller options async не разрешены, generic/synthetic mapping неизменен.
Источник: `docs/Post Media.txt` 269–275 (async), 117–126/161–166 и 285–355
(canonical MediaStatus id/object/status). Противоречивый prose «taskId» не
расширяет response parser: taskId-only, unsafe/missing id, unknown object/status,
malformed JSON и zero-image completed дают SUBMIT_UNCERTAIN без нового
ref/billing/POST. Canonical pending/processing ref подтверждается до GET;
immediate completed поддерживается. Timeout с known ref и restart/sync —
GET-only, без смены operation или угадывания endpoint. HTTP timeout30, domain,
DDL, gateway/normalizer/config/CLI и Registry subsets не меняются.

Уточнение — offline wire contract, не live acceptance: async response shape и
latency реально не проверены. Исходный patch SHA — `NOT_COMMITTED`; E06/E10
остаются OPEN, новые paid POST запрещены до отдельного разрешения. Проверки:
`test_polza_priced_routing.py`, `test_polza_mie_count.py`, `test_polza_gateway.py`,
`tests/integration/test_single_image.py`; owner —
[PROVIDER.polza-media](../../../instructions/PROVIDER.polza-media.instructions.md).
Наблюдения и lifetime accounting принадлежат release-board, не этому контракту.

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

- **ИП:** `01` — FTS5, статистика расходов и hash входных файлов «желательно»,
  `--detach`/удаление/манифесты — желательно (`01-product-scope.md`).
- **Решение:** FTS5 и сводка расходов по валютам — **обязательные** (E09);
  hash входных файлов (SHA-256) — **обязательный** пункт истории (E04/E09);
  `--detach`, удаление истории, универсальные batch manifests, публичные
  maintenance-команды — не реализуются в v0.1.
- **Документы:** `01`, [release-scope.md](../release-scope.md), `README`.
- **Проверка:** R20 (поиск и расходы проверяют полезный результат); R07 (история
  сохраняет hashes входов до перезапуска); сверка scope.

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
| D10 | Remote operation | `remote_job_id` + сохранённый `operation` в snapshot; endpoint не угадывается | 03, 05, 07 | R07 |
| D11 | Money/migrations | decimal TEXT ↔ Decimal; суммы в Python; один runner | 07 | R08, R18 |
| D12 | Raw help | Resolved MD без front matter/директив; ANSI нет | 09 | R17 |
| D13 | Exit codes | Сохранить 0/2–9; добавить 130 и 1 | 04 | R16 |
| D14 | Local ownership | Минимальный guard; схема и stale — до E08 | 08 | R24 |
| D15 | Scope | FTS5 + сводка расходов + hash входов обязательны; ряд функций не в v0.1 | 01, README | R07, R20 |
| D16 | Verified model evidence | YAML ≠ проверенная поддержка; фиксировать remote ID/источник/дату | 01, 06 | R04 |

Все 16 развилок имеют выбранный вариант; открытых блокеров уровня E00 нет.
Конкретные API (migration runner) и схема cross-process ownership уточняются на
своих этапах и не блокируют E00.

## Конкретизация D14 для связной E07–E09 поставки

По прямому заданию владельца используется минимальный local stdlib FS lock:
постоянный `locks/<job_id>.lock`, Windows byte-range `msvcrt` / POSIX `flock`,
nonblocking claim и перечитывание CREATED/full snapshot до submit. Kernel снимает
ownership при crash; файл не удаляется, PID/stale timeout не используется.
Никакого broker, daemon или distributed lock. `sync` использует тот же ownership
и только GET. Реализация принята offline в E07–E09 на `8a09d18`; Windows/Linux
и installed evidence перечислены в release-board. Cross-process/crash assertions
находятся в `test_execution_ownership.py`. E10 live/E11 открыты. Исходные решения
E00 выше сохранены.

## Уточнение D06/D07: документированный MIE count binding

По прямому заданию владельца добавляется только `gpt-5-4-image-2-mie` →
`openai/gpt-5.4-image-2@mie`, experimental/NOT_LIVE_VERIFIED. Источники —
публичные [guide](https://polza.ai/docs/gaidy/gpt-5-4-image-2.md) и
[model Markdown](https://polza.ai/models/openai/gpt-5.4-image-2.md), 2026-09-30.
Text-only count subset: logical max_images 1–4/default 1, только 1K; exact remote
mapper использует input.n. Generic mapping/Qwen/Gemini не расширяются.
Remote model identity допускает bounded ASCII qualifier, не URL; Polza adapter
проверяет единственный подтверждённый exact route. Logical IDs и opaque remote
job IDs/GET не меняются. RUB 4/image — MIE 1K metadata, не actual total и не цена
неквалифицированного/default-openai token-priced маршрута. Reference URLs/higher
resolutions известны API, но не проверены/не включены в этот subset.

## Уточнение D07: финансовый фильтр трёх Media bindings

По прямому заданию владельца фиксированный adapter rule для существующих exact
Qwen/Gemini/GPT MIE remote IDs добавляет top-level ProviderDto.only=[mie],
allow_fallbacks=false, max_price.image=целочисленный ROUND_CEILING опубликованного
maximum effective Decimal RUB pricing. Источники —
[Media create](https://polza.ai/docs/api-reference/media/create.md),
[Nano guide](https://polza.ai/docs/gaidy/nanobanano-2.md) и model Markdown:
цены Gemini MIE не ограничивают автоматические token-priced upstreams.
Missing/non-RUB/unusable pricing — безопасный typed отказ до HTTP.
Новых qualifiers, YAML schema, switches, retries или routing framework нет;
generic mapping неизменен. Price filter не actual billing/total/200 RUB guarantee;
usage и unknown reservations остаются отдельными. Это уточнение wire guard,
не live verification и не закрытие E10. Owner: PROVIDER.polza-media / REGISTRY.model-catalog.
Недостаточный пригодный normalized result сохраняет ref/billing и не COMPLETED;
sync остаётся GET-only. Zero-image malformed response сохраняет принятый C09
invalid-response/submit-uncertain контракт; новый billing не выдумывается,
ранее подтверждённые ref/cost не стираются. Один Job/paid POST, paid POST = 0.


## Change note: безопасная HTTP-диагностика Media (OFFLINE PRECOMMIT)

Доказанный локальный дефект: gateway сохранял HTTP status, но application
`_safe_error` при построении нового JobError стирал diagnostics. Принята узкая
application data policy: strict int 100–599 (не bool) в `details.http_status`,
конечный enum из локального `Post Media.txt` ApiErrorBodyPresenter в provider_code
(13 значений, включая api_key_revoked) и единственный документированный пример
reason=noProvidersForModel. Неизвестные/malformed tokens и вся raw/message/
headers/body/url/query/trace информация не сохраняются в Job/current CLI error.
Adapter дополнительно отклоняет даже известный токен, содержащий injected secret.

Это повторный контроль существующего JobError на границе хранения, без импорта
Polza в application/domain, нового DTO/DDL или diagnostics framework. Конечные
таблицы adapter/application намеренно независимы: первая контролирует недоверенный
HTTP и известный секрет, вторая — любой provider port перед persistence.
HTTP-классификация/retryability прежние: POST 408/5xx остаются SUBMIT_UNCERTAIN,
retryable=None, без нового ref/cost или автоматического POST. Current sync использует
тот же sanitizer и не подменяет прежнюю ошибку FAILED Job. Regression evidence —
`test_polza_error_diagnostics.py`, `test_single_image.py`, `test_complete_cli.py`.
Кандидат NOT_COMMITTED / NOT_LIVE_VERIFIED; исторические receipts не переписаны.


## Исторический change note до CN-05: canonical Media model + DTO count

По новому прямому заданию владельца после отдельных parent HTTP probes заменяется
только remote binding `gpt-5-4-image-2-mie`: `openai/gpt-5.4-image-2` вместо
`openai/gpt-5.4-image-2@mie`. Выбранный MIE по-прежнему задаётся через
provider.only=[mie], allow_fallbacks=false, max_price.image=4; async=true.
Logical ID/alias, цены, subset 1–4/default 1/text-only/1K/experimental неизменны.
Старый qualifier отклоняется до POST; persisted исторические remote IDs/ref/cost
не мигрируют и GET-only recovery не использует current Registry binding.

Это уточнение **заменяет wire часть** прежней записи D06/D07 «документированный
MIE count binding», не переписывая историческую запись. Локальный `Post Media.txt`
ImageInputDto документирует `input.max_images` (1–6); принимается это поле вместо
prior model-guide `input.n`, без расширения Registry до 6. `n` отдельного Images
endpoint не переносится, endpoint остаётся POST /api/v1/media. Generic/Qwen/Gemini
mapping, normalizer, deadlines, locks и DDL не меняются.

Parent сообщил отдельные sanitized direct HTTP observations вне source CLI:
qualified-n — 400 BAD_REQUEST, модель с @mie не найдена; base-n — canonical
pending → completed, ровно один image, output_units=1, actual cost 4 RUB при n=2.
Первое доказывает отказ qualifier **этого запроса**, не причину старого Job5/старого
capture; второе не доказывает count=2 или безопасность автоматического повтора.
Base-max также дал ровно один image/output_units=1/actual cost4 при max_images=2.
Ни n, ни DTO max_images не доказали Media count=2. Это открытый live blocker,
а не обещание исправленной множественной генерации. Их receipt names:
qualified-n-http.json, base-n-http.json, base-max-http.json в выделенном
`C:/PY/aimedia-polza-probes-2026-10-01/` (parent evidence, child не читал ключ/логи).
Запрошенные два при одном пригодном результате остаются FAILED
PROVIDER_INCOMPLETE_RESULT с known ref/cost; zero-image C09 не меняется.
Новый source NOT_COMMITTED / NOT_LIVE_VERIFIED; count=2 нового source остаётся
обязательным E06/E10 gate, без waiver. Старые receipts не переносятся на новый SHA.


Финальный scope этого OFFLINE кандидата подтверждён владельцем: canonical Media
base + DTO input.max_images + safe diagnostics, без Images adapter/нового model.
Дополнительные parent Images probes не решили count: qualified ID дал HTTP400
BAD_REQUEST/model not found; canonical base/top-level n=2 дал HTTP200 legacy
created/dataarray с одним image, cost4, без id. Это не основание переносить
Images response/endpoint в Media normalizer или расширять product routing.
Receipts: images-qualified-n-http.json, images-base-n-http.json в том же parent
probe root. На этом checkpoint lifetime 13 attempted POST, known33.6 +
unknown77 = 110.6/200 RUB. Пользователь разрешил отдельные diagnostic repeats;
прежний запрет новых MIE probes superseded, но автоматического Job retry,
редактирования старых Job2/Job5 и разрешения публикации это не даёт.


## CN-05 implementation: один output, все MIE настройки, несколько входных refs

[Прямой принятый scope CN-05](../release-scope.md) supersedes прежний mandatory
multi-output/count2/NO WAIVER gate. Multiple outputs одного submit отложены;
несколько input images + prompt не откладываются. Исторические note/receipts выше
остаются историей, не текущим требованием count2.

Canonical Media ID openai/gpt-5.4-image-2, fixed MIE only/noFallback/async=true.
В существующем GPT YAML включены documented MIE controls:1K/2K/4K,шесть ratios,
prompt≤5000,multiple refs≤16 PNG/JPEG/WebP,exact Decimal RUB tiers4/7/11;
новое max_images=1. Ceiling published max=11 исключает другие token-priced
upstreams, не является billing. Sources: parent full guide/model-page fetch
2026-10-01, local Post Media DTO. OpenAI extra ratios и undeclared quality/seed
не включаются; неизвестный max bytes не выдумывается. Other Registry YAML,
normalizer,ports/storage/DDL/deadlines unchanged.

Registry validator содержит только tiny exact binding condition,не DSL:
resolved/default auto только1K;1:1 не4K.15 допустимых pairs проверяются доPOST.
Existing reference snapshots/archive/base64 objects используются напрямую;
references являются inputs,не output count. Старые multi-output Job snapshots,
GET-only recovery/required-result integrity/E05 multi-artifact failure cases
сохраняются; tests явно создают disposable HISTORICAL snapshots,не новый
builtin count2 submit.

Parent observed single2K16:9 + two local PNG/JPEG refs completed/cost7;
continuation по known ref одинGET/нольPOST,original180s processing capture сохранён.
Receipt single-2k-two-refs-poll-known-http.json,parent evidence root. Это remote
proof данного режима,не committed-source CLI/all-settings live acceptance.
Lifetime checkpoint14POST,known40.6+unknown77=117.6/200;multiple-output probes
stopped by deferral. New source NOT_COMMITTED/NOT_LIVE_VERIFIED;E06/E10 OPEN для
CN-05 source/installed scenarios,E11 не авторизован.
