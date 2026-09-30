---
applyTo: "src/aimedia/domain/**, tests/architecture/**, tests/support/**, tests/unit/test_domain_invariants.py, tests/unit/test_serialization.py, tests/unit/test_fake_provider.py"
name: "CORE.DomainBoundary"
description: "Читай при изменении src/aimedia/domain, provider/storage ports, статусов Job, денежных значений, DTO requests/results или тестовых doubles: граница домена, инварианты состояний и денег, правило отсутствия заглушек в портах и место fake provider."
---

# CORE — Граница домена aimedia

Эта инструкция фиксирует **реализованный на C03 (E02)** контракт домена
(`src/aimedia/domain/`). Планы-источники — `docs/plans/02-system-architecture.md`
и `docs/plans/03-domain-model.md`; принятые развилки —
`docs/plans/decisions/implementation-baseline.md`.

## Что домен обязан соблюдать

`src/aimedia/domain/` — чистый слой: понятия, инварианты и порты без инфраструктуры.
Это проверяется не соглашением, а тестом на реальном графе импортов
(`tests/architecture/test_dependencies.py`).

- Домен **не импортирует** `typer`, `rich`, `httpx`, `peewee`, `yaml`, `PIL`,
  `platformdirs`, `pydantic_settings`, `asyncio`, `socket`, `sqlite3`, `urllib`,
  `requests`, `aiohttp`.
- Домен **не зависит** от модулей проекта `cli`, `application`, `config`, `logging`,
  `paths`, `providers`, `registry`, `storage`, `processing`, `search`.
- Домен **не выполняет IO**: вызовы `open`, `Path.read_*`, `Path.write_*`,
  `Path.iterdir` в пакете запрещены отдельным тестом.
- Домен **не знает** о секретах, настройках, URL provider или схеме SQLite.

`ProviderRef`/`ModelRef` — доменные значения, а не HTTP-клиент. Единое каноническое
имя удалённого задания — `remote_job_id`; тип операции (`RemoteOperation.MEDIA`) и
`remote_job_id` хранятся вместе, endpoint по строке ID не угадывается (D10).

## Инварианты состояний Job

`JobStatus` — `created`/`submitted`/`running`/`completed`/`failed`/`cancelled`;
terminal — `completed`/`failed`/`cancelled` (инвариант 13).

- Terminal Job не возвращается в non-terminal. Единственное исключение —
  `failed → completed` при recovery **того же** remote execution без новой генерации.
- Исключение выражается отдельным аргументом `recovery=True` в
  `can_transition`/`ensure_transition`, а не молчаливым расширением таблицы
  переходов. Промежуточный `failed → running` запрещён и при recovery.
- Прежняя ошибка recovery не стирается: она остаётся в `Job.recovery.previous_error`
  (D01), поэтому успешная финализация не маскирует прежний платный сбой (R23).
- `created` не несёт `submitted_at`/`completed_at`; `completed` не несёт terminal
  error; `failed` обязан иметь `error`; terminal Job обязан иметь `completed_at`.
- `completed` image Job требует `result` и хотя бы один локально сохранённый
  `Artifact(kind=IMAGE, role=FINAL)` в `result.artifacts` или `Job.artifacts`.
  Обе коллекции хранятся как неизменяемые tuple (входные списки принимаются,
  JSON по-прежнему содержит массивы), чтобы после валидации нельзя было удалить
  обязательный FINAL через мутацию списка. `ORIGINAL` от `--keep-original`
  остаётся partial artifact при сбое и сам по себе не завершает Job; recovery
  `failed → completed` требует того же FINAL.
- Недопустимый переход поднимает `InvalidJobStateTransitionError` до записи в
  историю, а не после.

## Деньги: unknown ≠ zero, валюты не смешиваются

- `Cost` — это `amount + currency`; валюта обязательна, если сумма известна.
- Отсутствие `Cost` (`cost is None`, `Job.has_known_cost is False`) — неизвестная
  цена; `Decimal("0")` — известная цена. Эти состояния не сводятся друг к другу (D11).
- `ExactDecimal` отклоняет `float` и `bool`: деньги не проходят через двоичную дробь.
- Суммы по валюте считаются точно независимо от текущей Decimal precision в
  Python (`total_by_currency`), а не SQL `SUM()`; RUB и USD не складываются и не
  конвертируются.
- `CostReport` — неизменяемый DTO read-only отчёта: `totals` по валютам,
  `total_jobs`, отдельные счётчики ненулевой известной стоимости, известного нуля
  и неизвестной цены. `CurrencyTotal.job_count` включает известный ноль. Порт
  `CostReportRepository.aggregate(start, end)` принимает только timezone-aware
  границы UTC-интервала `[start, end)` по `Job.created_at`; adapter
  `PeeweeCostReportRepository` читает одну строку на Job, сравнивает разобранные
  timestamps в Python и не превращает `usage.raw` в новое списание. Повреждённые
  timestamp/стоимость/валюта дают безопасную типизированную storage-ошибку без
  раскрытия содержимого строки.

## Сериализация домена (проверяется `tests/unit/test_serialization.py`)

- `Decimal` → строка без экспоненты и без потери trailing zeros в пределах точности;
- `datetime` → ISO-8601 UTC с суффиксом `Z`; timezone-naive значение отклоняется;
- `Path` → posix-строка, одинаковая на Windows и Linux;
- enum → его строковое значение;
- `sha256` нормализуется к нижнему регистру, MIME-тип — к нижнему, валюта — к верхнему.

Модели домена — `frozen=True`, `extra="forbid"`: лишнее поле не проскакивает молча.

## Порты и запрет заглушек

`src/aimedia/domain/ports.py` содержит контракты, реализуемые снаружи:
`ProviderGateway` (минимальный, обязательный `submit`), `PollingProviderGateway`
(опрос), `CancellableProviderGateway` (явная отмена), `JobRepository`,
`ArtifactStorage`, `CostReportRepository` (read-only агрегирование расходов).

- В портах **нет** методов-заглушек с `NotImplementedError`. Возможность provider
  выражается отдельным протоколом и `ProviderCapabilities`; application проверяет
  возможность, а не ловит исключение.
- Методы `ProviderGateway` асинхронны, методы `JobRepository`/`ArtifactStorage`
  синхронны: локальный SQLite не должен ждать remote внутри транзакции.
- Через границу provider проходит `ProviderResult`/`SubmissionResult`, а не сырой
  HTTP JSON и не `httpx.Response`. `SubmissionResult(completed)` несёт результат
  с доступным удалённым image artifact; `submitted`/`running` несёт `RemoteJobRef`
  с `operation` для опроса. Нормализованный отказ provider поднимается как
  `ProviderError` с `JobError`, а не возвращается состоянием `failed`.

## Место fake provider

`tests/support/fake_provider.py` — тестовый double, а не production provider.

- Каталог `tests/support` добавляется в `sys.path` только из `tests/conftest.py`;
  он не собирается pytest и не входит в `REQUIRED_SUITES`.
- Production-код не импортирует `tests`, `support`, `offline_policy`; это проверяет
  архитектурный тест. Fake **не регистрируется** в production registry.
- Fake реализует публичные порты как настоящий adapter и считает фактические
  `submit_count`/`fetch_count`/`cancel_count`, поэтому тесты могут утверждать, что
  запрещённое действие (повторный submit при recovery, remote cancel при Ctrl+C)
  действительно не выполнялось.

## Подготовка входов и prompt — C04, не C03

Домен определяет только DTO (`PromptSource`, `CompiledPrompt`, `InputRef`).
Чтение файлов, определение MIME, вычисление SHA-256 и компиляция prompt с
разделителем `\n\n` — работа C04 (`feat(inputs): preserve ordered prompt and
reference snapshots`) и живёт в `src/aimedia/application/`:

- `aimedia.application.prompts.compile` — `PromptCompiler` на одном упорядоченном
  списке источников; compiled prompt это ровно `"\n\n".join` снимков непустых
  источников;
- `aimedia.application.inputs.prepare` — `prepare_reference_images`: порядок refs в
  `InputRef.position`, MIME по фактическим байтам, `size_bytes` и `sha256` по тем
  же байтам;
- `aimedia.application.inputs.image_probe` — структурная проверка PNG/JPEG/WebP по
  содержимому (не по расширению), только стандартная библиотека.

Границы, которые нельзя ослаблять:

- файловый pipeline не возвращается в `aimedia.domain`; домен остаётся IO-free и
  проверяется `tests/architecture/test_dependencies.py` (включая запрет импорта
  `httpx`/`peewee`/`PIL`/`typer`/`yaml` из `aimedia.application`);
- снимок источника (`PromptSource.text`) и подготовленный `InputRef` не
  перечитываются задним числом: изменение файла на диске не меняет уже
  подготовленные данные (инвариант prompt history);
- будущая managed-копия входа (`CN-01`, планируется) не меняет границу домена:
  домен получает только необязательный относительный `InputRef.managed_path`, байты
  и файловые операции остаются в application-слое, а копия входа не становится
  `Artifact` результата ([07-storage-history-costs.md](../docs/plans/07-storage-history-costs.md),
  «Managed-копии reference images»);
- количественные лимиты модели приходят из Model Registry (E03) и остаются
  `None`, пока неизвестны; локально подтверждён только набор форматов
  `SUPPORTED_IMAGE_MIME_TYPES` (PNG/JPEG/WebP) — придуманные model-specific числа
  не подставляются (`06-model-registry.md`).
