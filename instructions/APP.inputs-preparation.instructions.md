---
applyTo: "src/aimedia/application/**, tests/unit/test_prompt_compiler.py, tests/unit/test_input_preparation.py, tests/unit/test_prepared_request_flow.py, tests/unit/test_input_events.py, tests/support/image_fixtures.py"
name: "APP.InputsPreparation"
description: "Читай при изменении src/aimedia/application/prompts, src/aimedia/application/inputs или их тестов: порядок и snapshot prompt-источников, подготовка reference images (MIME по содержимому, размер, SHA-256), лимиты без выдуманных model-specific чисел и границы application-слоя."
---

# APP — Подготовка prompt и входов (C04, E02)

Эта инструкция фиксирует **реализованный на C04** контракт подготовки входов до
provider submit. План-источники — `docs/plans/04-cli-contract.md`,
`docs/plans/03-domain-model.md`, `docs/plans/06-model-registry.md`,
`docs/plans/08-job-execution.md`; принятые развилки —
`docs/plans/decisions/implementation-baseline.md` (D04, D08, D15).

## Граница слоя

Подготовка — работа application-слоя (`src/aimedia/application/`), не домена.
Домен описывает только форму данных (`PromptSource`, `CompiledPrompt`, `InputRef`).

- Файловый IO, определение MIME и хеширование живут в
  `aimedia.application.prompts` и `aimedia.application.inputs`; домен остаётся
  IO-free и не импортирует `aimedia.application`.
- `aimedia.application` не импортирует `httpx`, `peewee`, `PIL`, `typer`, `yaml`,
  `platformdirs`, `pydantic_settings`, `rich`, `sqlite3`, `requests`, `aiohttp`
  и не зависит от `aimedia.cli`/`providers`/`registry`/`storage`/`search`.
  Проверяется `tests/architecture/test_dependencies.py`.
- Проверка структуры изображения использует только стандартную библиотеку
  (`zlib`, `struct`): Pillow остаётся зависимостью E05 (локальная конвертация), а не
  условием подготовки входа.

## Порядок и snapshot prompt (D04)

- `PromptCompiler.compile` принимает **один** упорядоченный список
  `PromptSourceRequest`; порядок определяет последовательность, а `position`
  присваивает компилятор. Два отдельных списка `--prompt`/`--prompt-file` с
  последующей склейкой контракт не реализуют.
- Compile prompt — ровно `PROMPT_SOURCE_SEPARATOR.join` (`"\n\n"`) снимков
  непустых источников. Текст не переформатируется; значимые пробелы и переводы
  строк сохраняются байт-в-байт после декодирования UTF-8.
- `PromptPreparation.sources` хранит снимок текста каждого источника (включая
  пустые, со своими позициями). Изменение файла на диске после подготовки **не**
  меняет ни `compiled.text`, ни `sources[i].text`: история показывает текст,
  который реально был подготовлен. Повторная компиляция читает актуальный файл —
  подготовка не является кэшем.
- Файл из одного whitespace считается пустым источником и не создаёт двойных
  разделителей. Если все источники пусты — `PromptRequiredError`
  (`PROMPT_REQUIRED`), а не пустой prompt модели.

## Подготовка reference images (D08, D15)

`prepare_reference_images` принимает упорядоченную последовательность путей:

- `InputRef.position` — порядок `--image`, а не внешнее поле: порядок references
  значим для моделей с несколькими референсами;
- `mime_type` определяется по сигнатуре и структуре контейнера, **не** по
  расширению: текстовый файл с `.png` и обрезанное изображение отклоняются
  (`UnsupportedInputFormatError`, `UNSUPPORTED_INPUT_FORMAT`);
- `size_bytes` — фактическая длина файла, `sha256` — SHA-256 по тем же байтам
  (обязательный пункт истории, D15); размеры изображения в `metadata`;
- подготовленные данные не перечитываются задним числом: изменение файла после
  подготовки не меняет hash/MIME уже подготовленного `InputRef`.

Подтверждённые локально форматы — PNG, JPEG и WebP
(`SUPPORTED_IMAGE_MIME_TYPES`). Это локальная поддержка проекта, а не выдуманное
ограничение конкретной модели. Расширенный WebP принимается только при наличии
фактического кадра VP8/VP8L: заголовок VP8X без кадра и анимированный WebP
(VP8X + ANIM/ANMF) отклоняются как `UNSUPPORTED_INPUT_FORMAT` до submit.

## Лимиты без выдуманных чисел

- `ReferenceLimits.max_references` и `max_size_bytes` приходят из Model Registry
  (E03) и по умолчанию равны `None`: неизвестный лимит не превращается в
  «разумное» число (`06-model-registry.md`, «Input constraints не должны
  выдумываться»).
- `allowed_mime_types` по умолчанию — локально подтверждённый набор и может быть
  сужен provider-ограничением; явно заданный набор действительно отклоняет вход.

## Понятные pre-submit ошибки

Подготовка выполняется до любого provider submit и выражается существующими
доменными ошибками, а не сырым трейсбеком:

| Ситуация | Ошибка / код |
|---|---|
| prompt-файл недоступен | `InputFileNotFoundError` / `INPUT_FILE_NOT_FOUND` |
| prompt-файл не UTF-8 | `InvalidParameterValueError` / `INVALID_PARAMETER_VALUE` |
| все источники пусты | `PromptRequiredError` / `PROMPT_REQUIRED` |
| reference image недоступна | `InputFileNotFoundError` / `INPUT_FILE_NOT_FOUND` |
| reference не изображение/повреждена | `UnsupportedInputFormatError` / `UNSUPPORTED_INPUT_FORMAT` |
| превышен явный лимит числа | `TooManyReferenceImagesError` / `TOO_MANY_REFERENCE_IMAGES` |
| превышен явный лимит размера | `InvalidParameterValueError` / `INVALID_PARAMETER_VALUE` |

## Batch и изоляция Job

Batch — набор независимых Jobs, а не один Job с общим prompt. Каждый вызов
компиляции/подготовки возвращает собственный неизменяемый снимок; prompt одного
Job и его reference images не смешиваются с batch-элементами соседнего.

## Диагностика

`compile` и `prepare_reference_images` принимают необязательный
`aimedia.logging.EventLogger` (чистые преобразования не обязаны логировать):

- `prompt_compiled` — числа источников и длина текста, без самого prompt;
- `input_prepared` — число, суммарный размер и набор MIME, без путей и содержимого;
- `validation_failed` — код доменной ошибки уровня WARNING.

Полный prompt, содержимое и пути файлов в диагностику не попадают
(`docs/plans/logging-contract.md`).

## Что не входит в C04

CLI-парсер (`--prompt`/`--prompt-file`/`--image` как Typer-опции) и Polza adapter —
этапы E09 и E06. Локальная конвертация изображений и `ArtifactStorage` — E05.
Model-aware validation (число refs по модели, разрешённые значения) — E03. Здесь
описан только application-контракт подготовки данных, на который эти этапы
опираются.

## Обязательные проверки

```bash
uv run --locked --no-env-file pytest tests/unit/test_prompt_compiler.py tests/unit/test_input_preparation.py
uv run --locked --no-env-file pytest tests/unit/test_prepared_request_flow.py tests/unit/test_input_events.py
uv run --locked --no-env-file pytest tests/architecture/test_dependencies.py
uv run --locked --no-env-file python scripts/quality.py quick
```
