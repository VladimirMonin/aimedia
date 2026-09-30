---
applyTo: "src/aimedia/registry/**, tests/contracts/**, tests/unit/test_registry_*.py, tests/security/test_registry_data_only.py"
name: "REGISTRY.ModelCatalog"
description: "Читай при изменении src/aimedia/registry или его тестов: строгая data-only загрузка YAML, alias/effective resolution, provider override, единый источник допустимых значений для validation и help/JSON, происхождение сведений (documented vs live_verified), безопасные события Registry и documented experimental production bindings."
---

# REGISTRY — Model Registry (E03, C05)

Эта инструкция фиксирует **реализованный на E03** контракт каталога моделей.
План-источники — `docs/plans/06-model-registry.md`,
`docs/plans/04-cli-contract.md`, `docs/plans/logging-contract.md`,
`docs/plans/README.md` (E03); принятые развилки —
`docs/plans/decisions/implementation-baseline.md` (D06, D07, D08, D16).

## Граница пакета

`src/aimedia/registry/` — инфраструктурный слой над декларативными YAML-данными.
Он offline: не ходит в сеть, не знает provider config (ключи, base URL, timeout),
не содержит Typer/Rich и не выполняет HTTP. Домен (`aimedia.domain`) остаётся
IO-free и не зависит от Registry; проверяет `tests/architecture/test_dependencies.py`.

- `loader.py` — строгий data-only разбор: разрешены только скалярные теги ядра,
  запрещены `%TAG`/`!!python/...` и дублирующиеся ключи; одна модель — один
  документ; неизвестная `schema_version` — явная ошибка, а не permissive fallback.
- `models.py` — неизменяемые DTO (`frozen=True`, `extra="forbid"`).
- `resolver.py` — alias → canonical ID и построение `EffectiveModelDefinition` как
  `base model + provider overrides`; override может только **сужать** базу, иначе
  `InvalidProviderOverrideError`, а не «победа последней».
- `validator.py` — `validate_model_request` проверяет request против уже
  построенной effective definition и возвращает её же adapter'у.
- `views.py` — публичное представление (`build_model_view`/`build_model_list`/
  `render_model_help`) для будущих `models show/list` (E09).
- `events.py` — безопасные события application-границы.
- `builtin.py` + `data/` — встроенный каталог как package resource.

## Единый источник допустимых значений 🔒

Help, JSON и validation читают **один и тот же** `EffectiveModelDefinition`.
Второй таблицы допустимых значений (hardcoded список в CLI/help) быть не должно:
изменение provider override обязано согласованно менять и проверку, и рендер.
Проверяется `tests/contracts/test_model_views.py` (E03, «Изменение Registry меняет
validation и model JSON согласованно»).

Неизвестное ограничение остаётся `null` (`None`) и не превращается в придуманное
число: из известной границы `None` не следует ни «ноль», ни «без ограничений».

Локальный конечный формат (`final_format`, включая WebP из локального processing)
**не** объявляется native-возможностью модели: provider output и локальная
конвертация разделены (D08, `06-model-registry.md`, «Output format: provider vs
local»). View не показывает `webp` там, где модель документирует только `png`/`jpeg`.

## Происхождение сведений: documented vs live_verified 🕒

Наличие YAML-записи **не** означает проверенную поддержку модели (D16).
Registry v0.1 различает только:

- `documented` — запись с `verification.checked_at` и `verification.source`;
- `unverified` — запись без провенанса (`ProvenanceStatus.UNVERIFIED`).

Статус `live_verified` (подтверждение реальным вызовом provider) в схеме v0.1
**отсутствует и не выдумывается**: для него нужны отдельные live-evidence
(release-scope, R04), а не запись в каталоге. Не добавляй это значение, пока не
появится источник, отличающий «есть в Registry» от «проверена живым вызовом».

## Встроенный каталог и реальные данные 🚧

- Production YAML (`src/aimedia/registry/data/`, package resource) содержит только
  записи с **authoritative** источником: exact `remote_model_id`, лимиты и
  capabilities подтверждены публичной документацией; к записи приложены
  `verification.source` и дата.
- Демонстрационные ID из `docs/plans/01`–`09` (`seedream-5-pro`,
  `gpt-image-2.5`, `qwen-image-2.1`, `some/provider/model`) — **условные**: они не
  переносятся в production каталог без проверки.
- На E03 каталог был пуст. После authoritative public catalog GET добавлены только
  `qwen/image-2.1` и `google/gemini-3.1-flash-image-preview`, с источником/датой,
  **experimental**, без live_verified/active claims. Gemini refs консервативно ≤8
  (Guide), несмотря на catalog 14. Явное использование требует
  `--allow-experimental`; missing unit parameter не выдумывается, >1 output
  отклоняется adapter до POST. Синтетические IDs остаются только в tests.
- `CatalogPricing` — typed exact Decimal metadata RUB по resolution; опубликованный
  максимум вычисляется из текущих tiers, не гарантия будущей цены. Цены не участвуют
  в actual billing. View/help читают те же pricing/limits effective definition.
- Загрузка встроенного каталога идёт через `importlib.resources`
  (`builtin_registry_dir`), а не через относительный путь: loader не должен
  зависеть от текущей рабочей директории.

## Историческая воспроизводимость 📚

Удаление модели из текущего Registry не ломает чтение старого Job: история хранит
snapshot `model_id`/`remote_model_id`/provider, поэтому `jobs show` читается из
сохранённых данных **без** обращения к resolver'у
(`06-model-registry.md`, «Историческая воспроизводимость»). Проверяется тестом
сериализации Job против пустого каталога.

## Диагностические события 📜

Application-граница логирует (необязательный `EventLogger`, чистые функции без
логгера молчат):

- `registry_loaded` — `count` и детерминированный `digest` данных; **без путей**
  файлов Registry (digest считается по сериализованным записям, порядок не влияет);
- `model_resolved` — `model_id`/`requested_model`/`provider_id`/`remote_model_id`;
- `model_validation_failed` — только `code` и `parameter`, **без** prompt,
  содержимого изображений и путей источников.

Границы полей — в `docs/plans/logging-contract.md`. Новые детали события не
добавляй, не расширив сначала контракт логирования.

## Обязательные проверки

- `tests/unit/test_registry_loader.py`, `tests/security/test_registry_data_only.py` —
  строгость и безопасность разбора YAML.
- `tests/unit/test_model_resolution.py`, `tests/unit/test_model_validation.py` —
  alias/override/валидация; недопустимый request останавливается до submit.
- `tests/contracts/test_model_views.py` — единый источник значений для view/help/JSON.
- `tests/unit/test_registry_events.py` — безопасность и детерминированность событий.
- `tests/unit/test_serialization.py` — историческая воспроизводимость Job.

CLI-команды `models list/show` используют эти views; owner —
[CLI.public-image](CLI.public-image.instructions.md). Help dynamic blocks —
[HELP.atomic-resources](HELP.atomic-resources.instructions.md).
