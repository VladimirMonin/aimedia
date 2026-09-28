# 04. CLI Contract — внешний контракт командной строки AI CLI-платформы

> [!abstract] Назначение документа
> Этот документ фиксирует **публичный CLI-контракт** проекта: структуру команд, правила аргументов, поведение human/JSON режимов, соглашения по вводу файлов, обработке промптов, ошибкам, exit codes, help и стабильности интерфейса.
>
> Документ отвечает на вопрос **«как человек и AI-агент взаимодействуют с приложением через CLI»**.
>
> Он не описывает внутреннюю реализацию use cases, provider adapters, SQL-схему, миграции или YAML schema моделей. Эти детали раскрываются в других фундаментальных документах.

---

## Статус документа 🧭

| Поле | Значение |
|---|---|
| Номер | `04` |
| Название | `CLI Contract` |
| Статус | Draft / Foundation |
| Область | Публичный интерфейс командной строки |
| Основная версия | `v0.1` |
| Рабочая команда | `aimedia` |
| Основной сценарий v0.1 | Image generation |
| Human output | Rich / человекочитаемый |
| Agent output | JSON / стабильный машинный формат |
| Help | Встроенный CLI + Markdown docs |

---

## Цель CLI-контракта 🎯

CLI должна быть одновременно:

- удобной человеку;
- предсказуемой для AI-агента;
- стабильной для автоматизации;
- достаточно выразительной для разных типов AI-задач;
- простой для первой версии;
- расширяемой без слома существующих команд.

CLI рассматривается как **публичный API продукта**.

Это означает, что имена команд, семантика аргументов, JSON-вывод и exit codes должны изменяться осознанно.

> [!important]
> CLI не является «обёрткой вокруг Python-функций».
>
> Это самостоятельный контракт, который должен оставаться понятным и стабильным при изменении внутренней реализации.

---

# Основной принцип команд 🧩

Команды строятся по схеме:

```text
aimedia <domain> <action> [arguments] [options]
```

Примеры:

```bash
aimedia image generate
aimedia image batch

aimedia jobs recent
aimedia jobs show
aimedia jobs retry
aimedia jobs sync

aimedia models list
aimedia models show

aimedia help image.generate
```

В будущем:

```bash
aimedia audio transcribe
aimedia audio speech

aimedia text generate

aimedia embedding create

aimedia search query
```

---

## Почему команды группируются по доменам

Плохой вариант:

```text
aimedia generate-image
aimedia transcribe-audio
aimedia speech
aimedia retry-job
aimedia list-models
```

Он быстро превращается в плоский список десятков команд.

Предпочтительный вариант:

```text
image
audio
text
embedding
jobs
models
search
help
```

Каждый домен получает свои действия.

---

# Дерево CLI v0.1 🌳

```text
aimedia
├── image
│   ├── generate
│   └── batch
│
├── jobs
│   ├── recent
│   ├── show
│   ├── retry
│   ├── sync
│   └── search        # если FTS5 входит в раннюю версию
│
├── models
│   ├── list
│   └── show
│
├── providers
│   └── list
│
├── help
│
└── version
```

Будущие ветки:

```text
aimedia
├── audio
│   ├── transcribe
│   └── speech
│
├── text
│   └── generate
│
├── embedding
│   └── create
│
└── search
    └── query
```

---

# Общие правила синтаксиса ⌨️

## Длинные флаги

Основной публичный интерфейс использует длинные флаги:

```text
--prompt
--prompt-file
--image
--model
--provider
--resolution
--aspect-ratio
--format
--out
--json
```

Короткие формы допустимы только для действительно очевидных и часто используемых параметров.

Например:

```text
-o → --out
```

Но v0.1 не обязана вводить короткие aliases вообще.

---

## kebab-case

Все multi-word флаги используют:

```text
kebab-case
```

Например:

```text
--prompt-file
--aspect-ratio
--max-images
--keep-original
```

Не используются:

```text
--prompt_file
--aspectRatio
```

---

## Повторяемые опции

Если параметр допускает несколько значений, одна и та же опция повторяется:

```bash
--image face.png \
--image clothes.png \
--image background.png
```

Аналогично:

```bash
--prompt-file base.md \
--prompt-file character.md \
--prompt-file scene.md
```

Это предпочтительнее строки со списком через запятую.

---

# Общие глобальные опции 🌐

Концептуально CLI должна поддерживать несколько глобальных параметров.

```bash
aimedia [GLOBAL OPTIONS] <command>
```

Рекомендуемый набор:

| Опция | Назначение |
|---|---|
| `--json` | Машинно читаемый вывод |
| `--quiet` | Минимум текстового вывода |
| `--verbose` | Расширенный диагностический вывод |
| `--provider` | Явный выбор provider |
| `--config` | Альтернативный config file |
| `--no-color` | Отключить ANSI/Rich оформление |

Не все опции обязаны быть реализованы в первом коммите.

---

## `--json`

`--json` является ключевым контрактом для AI-агентов и автоматизации.

В этом режиме:

- нет progress bar;
- нет ANSI;
- нет декоративных таблиц;
- нет Markdown rendering;
- stdout содержит только валидный JSON;
- диагностические логи отправляются в stderr;
- структура ответа должна быть стабильной.

---

# `image generate` 🖼️

Основная команда первой версии.

```bash
aimedia image generate [OPTIONS]
```

---

## Минимальный вызов

```bash
aimedia image generate \
  --prompt "A laboratory robot standing near a workbench" \
  --model seedream-5-pro
```

---

## Основные параметры

| Опция | Тип | Повторяемая | Обязательность | Назначение |
|---|---|---:|---:|---|
| `--prompt` | string | Да | Условно | Inline prompt |
| `--prompt-file` | path | Да | Условно | Prompt из файла |
| `--image` | path | Да | Нет | Reference image |
| `--model` | string | Нет | Да* | Логическая модель |
| `--provider` | string | Нет | Нет | Provider override |
| `--resolution` | string | Нет | Нет | 1K / 2K / 4K и т. п. |
| `--aspect-ratio` | string | Нет | Нет | Соотношение сторон |
| `--quality` | string | Нет | Нет | Качество |
| `--format` | string | Нет | Нет | Финальный локальный формат |
| `--max-images` | int | Нет | Нет | Число outputs |
| `--seed` | int | Нет | Нет | Seed, если поддерживается |
| `--out` | path | Нет | Нет | Output directory |
| `--name` | string | Нет | Нет | Базовое имя результата |
| `--keep-original` | bool | Нет | Нет | Сохранить provider-original |
| `--json` | bool | Нет | Нет | JSON output |

`*` Если позже будет введена default model, `--model` может стать необязательным. До появления явно зафиксированного default model параметр считается обязательным.

---

# Правила промпта 📝

## Источники промпта

Команда может принимать несколько источников:

```text
--prompt
--prompt-file
```

Оба параметра могут использоваться несколько раз.

Пример:

```bash
aimedia image generate \
  --prompt-file base.md \
  --prompt-file character.md \
  --prompt "Full body, frontal view" \
  --model seedream-5-pro
```

---

## Порядок объединения

Источники объединяются **строго в порядке появления в CLI**.

Например:

```bash
--prompt-file base.md \
--prompt "Instruction A" \
--prompt-file scene.md
```

даёт:

```text
base.md
↓
Instruction A
↓
scene.md
```

---

## Разделитель

По умолчанию между источниками добавляется:

```text
\n\n
```

То есть два перевода строки.

Это обеспечивает читаемое объединение markdown/text фрагментов.

---

## Пример compiled prompt

```text
[base.md content]

Instruction A

[scene.md content]
```

---

## Пустые источники

Файл, содержащий только whitespace, считается пустым.

Если все источники пустые:

```text
PromptRequired
```

---

## Кодировка

Текстовые prompt files в v0.1 ожидаются в:

```text
UTF-8
```

При невозможности декодирования CLI должна вернуть понятную ошибку.

---

# Правила `--prompt-file` 📄

Поддерживаемые расширения не должны жёстко ограничивать смысл.

Допускаются:

```text
.txt
.md
.prompt
```

и другие текстовые файлы, если они декодируются как UTF-8.

Расширение файла не определяет формат запроса модели.

---

# Правила `--image` 🖼️

Каждый `--image` добавляет один reference image.

Пример:

```bash
--image refs/face.png \
--image refs/clothes.webp \
--image refs/lab.jpg
```

CLI должна:

1. проверить существование файла;
2. определить/проверить MIME;
3. проверить, что файл действительно является поддерживаемым image input;
4. проверить число references по Model Registry;
5. передать нормализованный список в application layer.

---

## Порядок reference images

Порядок `--image` сохраняется.

Это важно для моделей, где порядок references может иметь значение.

---

# `--model` 🧬

CLI принимает **логический model ID**, определённый Model Registry.

Например:

```bash
--model seedream-5-pro
```

Не следует требовать от пользователя provider-specific remote ID, если registry уже знает соответствие.

---

## Alias моделей

Если registry поддерживает aliases:

```text
seedream5
seedream-5
seedream-5-pro
```

CLI может принимать alias.

Но в JSON/history желательно возвращать канонический ID.

---

# `--provider` 🌐

Provider может задаваться явно:

```bash
--provider polza
```

Если параметр отсутствует:

1. используется provider из user config;
2. либо default provider приложения;
3. либо CLI возвращает ошибку, если выбор неоднозначен.

Поведение должно быть детерминированным.

---

# `--resolution` 📐

Значение:

```text
1K
2K
4K
```

или иное, если модель поддерживает другой набор.

CLI не должна сама хранить полный список допустимых значений.

Проверка выполняется через Model Registry.

---

# `--aspect-ratio` 📏

Пример:

```bash
--aspect-ratio 16:9
```

CLI передаёт строковое значение в domain request после validation.

Допустимые значения определяет модель.

---

# `--format` 📦

`--format` означает **финальный локальный формат результата**.

Например:

```bash
--format webp
```

Это не обязательно идентично provider output format.

Если provider возвращает PNG, но пользователь запросил WebP:

```text
provider PNG
↓
local conversion
↓
final WEBP
```

---

## Поддерживаемые локальные форматы v0.1

```text
png
jpeg
webp
```

Alias:

```text
jpg
```

нормализуется в:

```text
jpeg
```

---

# `--out` 📁

`--out` задаёт директорию сохранения результата.

Пример:

```bash
--out D:\Projects\Comic\Scientist
```

Если директория не существует, приложение может создать её автоматически.

Ошибка создания директории должна быть явной.

---

## Default output path

Если `--out` не указан, используется системная output directory приложения.

Концептуально:

```text
<app_data>/outputs/YYYY-MM-DD/job_<id>/
```

Точный path layout фиксируется в storage-документе.

---

# `--name` 🏷️

`--name` задаёт базовое имя результата.

Пример:

```bash
--name scientist-front
```

При одном результате:

```text
scientist-front.webp
```

При нескольких:

```text
scientist-front_001.webp
scientist-front_002.webp
scientist-front_003.webp
```

Если `--name` не задан, используется системное имя.

---

# `--max-images` 🔢

Указывает желаемое число результатов.

Пример:

```bash
--max-images 4
```

Значение валидируется по capability модели.

---

# `--seed` 🎲

Передаётся только если модель поддерживает seed.

Если модель не поддерживает параметр:

```text
UnsupportedParameter
```

CLI не должна молча игнорировать пользовательский `--seed`.

---

# `--quality` 🎚️

`--quality` является model-aware параметром.

Например:

```text
basic
medium
high
```

Но набор допустимых значений определяет registry.

---

# `image batch` 📦

Команда для нескольких независимых image Jobs.

```bash
aimedia image batch [INPUTS] [OPTIONS]
```

---

## Основной сценарий

```bash
aimedia image batch prompts/*.md \
  --model seedream-5-pro \
  --concurrency 4
```

Каждый prompt file создаёт отдельный Job.

---

## Batch не объединяет prompts

Это принципиальное отличие:

```text
image generate
+
несколько --prompt-file
=
один Job, один compiled prompt
```

А:

```text
image batch
+
несколько файлов
=
несколько Jobs
```

---

## `--concurrency`

Пример:

```bash
--concurrency 4
```

Означает максимум четыре одновременно выполняющихся Job.

Это не размер persistent queue.

---

## Batch output

Human mode:

```text
5 jobs submitted

#101 completed
#102 completed
#103 failed
#104 completed
#105 completed

4 completed, 1 failed
```

JSON mode должен вернуть массив результатов.

---

# `jobs recent` 🕘

Показывает последние Jobs.

```bash
aimedia jobs recent
```

Опционально:

```bash
aimedia jobs recent --limit 20
aimedia jobs recent --status failed
aimedia jobs recent --model seedream-5-pro
aimedia jobs recent --json
```

---

## Human output

Пример:

```text
ID    STATUS      KIND             MODEL             COST       CREATED
481   completed   image.generate   seedream-5-pro    4.00 RUB   12:41
480   failed      image.generate   qwen-image-2.1    —          12:38
```

---

# `jobs show` 🔎

```bash
aimedia jobs show 481
```

Должна показывать:

- Job ID;
- kind;
- status;
- model;
- provider;
- timestamps;
- prompt;
- prompt sources;
- inputs;
- parameters;
- remote ID;
- usage;
- cost;
- artifacts;
- error.

---

## JSON mode

```bash
aimedia jobs show 481 --json
```

возвращает полный структурированный Job view.

---

# `jobs retry` ♻️

```bash
aimedia jobs retry 481
```

Retry создаёт **новый Job**.

Старый Job не изменяется.

Human output:

```text
Created job #512 as retry of #481
```

JSON:

```json
{
  "ok": true,
  "job_id": 512,
  "retry_of": 481
}
```

---

## Override параметров при retry

Полезно разрешить:

```bash
aimedia jobs retry 481 \
  --resolution 2K \
  --provider polza
```

Но только если override явно поддержан CLI.

Не следует позволять произвольное редактирование Job через retry без спецификации.

---

# `jobs sync` 🔄

Синхронизирует незавершённые Jobs с provider.

```bash
aimedia jobs sync
```

Опционально:

```bash
aimedia jobs sync 481
aimedia jobs sync --all
```

Команда:

- проверяет remote status;
- обновляет Job;
- скачивает готовые artifacts;
- обновляет usage/cost;
- завершает recovery.

---

# `jobs search` 🔍

Если FTS5 входит в раннюю реализацию:

```bash
aimedia jobs search "laboratory robot"
```

В v0.1 поиск может быть lexical-only.

Semantic search не входит в обязательный CLI-контракт первой версии.

---

# `models list` 📚

```bash
aimedia models list
```

Показывает модели из registry.

Пример human output:

```text
MODEL                FAMILY   PROVIDERS   STATUS
seedream-5-pro       image    polza       available
qwen-image-2.1       image    polza       available
gpt-image-2.5        image    polza       available
```

---

## Фильтры

В будущем или сразу:

```bash
aimedia models list --family image
aimedia models list --provider polza
aimedia models list --json
```

---

# `models show` 🧠

```bash
aimedia models show seedream-5-pro
```

Показывает capabilities.

Пример:

```text
Seedream 5.0 Pro

Capabilities
  text-to-image: yes
  image-to-image: yes
  multi-reference: yes

Resolution
  1K
  2K
  4K

Aspect ratio
  1:1
  16:9
  9:16

Output formats
  png
  jpeg
  webp
```

---

## JSON mode

```bash
aimedia models show seedream-5-pro --json
```

возвращает структурированные данные registry.

Это ключевой сценарий для AI-агента.

---

# `providers list` 🔌

```bash
aimedia providers list
```

Показывает доступные adapters.

Например:

```text
polza    configured
openai   not configured
```

v0.1 может иметь только `polza`.

---

# `help` 📖

Встроенная документация использует атомарные Markdown docs.

```bash
aimedia help image.generate
```

рендерит Markdown человеку.

---

## Raw mode

```bash
aimedia help image.generate --raw
```

возвращает исходный Markdown.

Это предназначено прежде всего для AI-агентов.

---

## JSON mode help

Опционально:

```bash
aimedia help image.generate --json
```

Пример:

```json
{
  "topic": "image.generate",
  "title": "Image generation",
  "markdown": "...",
  "related": [
    "image.references",
    "image.batch"
  ]
}
```

---

# Обычный `--help` 🪶

Стандартный:

```bash
aimedia image generate --help
```

должен оставаться коротким.

Пример:

```text
Usage:
  aimedia image generate [OPTIONS]

Options:
  --prompt TEXT
  --prompt-file PATH
  --image PATH
  --model TEXT
  --provider TEXT
  --resolution TEXT
  --aspect-ratio TEXT
  --format TEXT
  --out PATH
  --json

More information:
  aimedia help image.generate
```

То есть:

```text
--help
→ краткий command reference

aimedia help <topic>
→ полная Markdown документация
```

---

# Human mode 👤

Human mode — default.

Разрешены:

- Rich formatting;
- progress bars;
- spinners;
- таблицы;
- эмодзи;
- подсказки;
- summary после выполнения.

---

## Пример успешного Human output

```text
✓ Generation completed

Job:       #481
Provider:  polza
Model:     seedream-5-pro
Time:      18.4 s
Cost:      4.00 RUB

Output:
D:\AI-Media\outputs\481\scientist-front.webp
```

---

# Agent mode 🤖

Agent mode активируется через:

```bash
--json
```

Требования:

- stdout = только JSON;
- без Rich markup;
- без spinner;
- без progress bars;
- без поясняющего текста до/после JSON;
- стабильные имена полей;
- exit code соответствует результату.

---

## JSON success envelope

Рекомендуемая базовая структура:

```json
{
  "ok": true,
  "data": {}
}
```

---

## JSON error envelope

```json
{
  "ok": false,
  "error": {
    "code": "UNSUPPORTED_PARAMETER",
    "message": "Model does not support resolution 4K.",
    "details": {
      "parameter": "resolution",
      "allowed": ["1K", "2K"]
    }
  }
}
```

---

# JSON schema stability 🔒

Для automation JSON output должен рассматриваться как более стабильный контракт, чем human output.

Human output может улучшаться визуально.

JSON fields не должны переименовываться без версии/миграции контракта.

---

# stdout и stderr 📤

## stdout

Используется для основного результата.

Human mode:

```text
таблицы
итог
пути
```

JSON mode:

```text
ровно один JSON document
```

## stderr

Используется для:

- verbose diagnostics;
- warnings;
- logs;
- stack trace при debug mode.

В JSON mode warnings не должны ломать JSON stdout.

---

# Exit codes 🚪

Рекомендуемый базовый контракт:

| Exit code | Значение |
|---:|---|
| `0` | Success |
| `2` | CLI usage / invalid arguments |
| `3` | Domain validation error |
| `4` | Provider/configuration error |
| `5` | Remote generation failed |
| `6` | Timeout |
| `7` | Local filesystem/artifact error |
| `8` | Database/storage error |
| `9` | Partial batch failure |

---

## Batch exit code

Рекомендуется:

```text
0
→ все Jobs успешны

9
→ batch выполнен, но часть Jobs failed

5/6/7...
→ batch не удалось нормально запустить вообще
```

---

# Error codes 🧯

JSON error должен содержать стабильный строковый code.

Примеры:

```text
INVALID_ARGUMENT
UNKNOWN_MODEL
UNKNOWN_PROVIDER
UNSUPPORTED_PARAMETER
INVALID_PARAMETER_VALUE
PROMPT_REQUIRED
INPUT_FILE_NOT_FOUND
UNSUPPORTED_INPUT_FORMAT
TOO_MANY_REFERENCE_IMAGES
PROVIDER_AUTH_ERROR
PROVIDER_RATE_LIMIT
INSUFFICIENT_BALANCE
PROVIDER_TIMEOUT
REMOTE_GENERATION_FAILED
ARTIFACT_DOWNLOAD_FAILED
OUTPUT_WRITE_FAILED
DATABASE_ERROR
```

---

# Ошибки должны быть actionable 🎯

Плохо:

```text
Error 400
```

Хорошо:

```text
Model qwen-image-2.1 does not support resolution 4K.

Supported:
1K
2K
```

JSON:

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_PARAMETER_VALUE",
    "message": "Model does not support resolution 4K.",
    "details": {
      "parameter": "resolution",
      "value": "4K",
      "allowed": ["1K", "2K"]
    }
  }
}
```

---

# Warnings ⚠️

Warnings не должны завершать команду с ошибкой, если операция может корректно продолжаться.

Например:

```text
Provider ignored optional enhancement parameter.
```

Human mode:

```text
⚠ Parameter 'enhance' is not supported by this provider and was ignored.
```

Agent mode:

```json
{
  "ok": true,
  "data": {...},
  "warnings": [
    {
      "code": "PARAMETER_IGNORED",
      "message": "..."
    }
  ]
}
```

---

# Нельзя молча игнорировать явные пользовательские параметры 🚫

Если пользователь явно передал:

```bash
--seed 42
```

а модель не поддерживает seed, CLI должна:

```text
ошибка
```

а не молча выбросить параметр.

Исключение допустимо только для документированного fallback behavior.

---

# Конфигурация и CLI precedence ⚙️

Приоритет значений:

```text
CLI argument
↓
user config
↓
model default
↓
application default
```

То есть явный CLI input всегда имеет наивысший приоритет.

---

## Пример

Config:

```toml
[image]
default_format = "webp"
```

CLI:

```bash
--format png
```

Результат:

```text
png
```

---

# Environment variables 🌱

Secrets и некоторые runtime параметры могут приходить из environment.

Например:

```text
POLZA_API_KEY
```

Но environment variables не должны неожиданно переопределять явно заданные CLI параметры, кроме специально документированных secrets.

---

# Работа с путями 🗂️

CLI должна принимать:

- относительные пути;
- абсолютные пути;
- Windows paths;
- POSIX paths.

Внутренне пути нормализуются через `pathlib.Path`.

---

## Shell globbing

Команда:

```bash
aimedia image batch prompts/*.md
```

может зависеть от shell expansion.

На Windows поведение globbing может отличаться.

Поэтому приложение желательно должно уметь самостоятельно обрабатывать glob patterns там, где это часть контракта batch-команды.

---

# Имена файлов output 🏷️

Если пользователь не указал `--name`, имя должно быть:

- детерминированным внутри Job;
- уникальным;
- безопасным для файловой системы.

Например:

```text
result_001.webp
```

в директории:

```text
job_481/
```

Это лучше, чем случайные длинные имена.

---

# Existing output policy ♻️

Поведение при существующем файле должно быть безопасным.

Рекомендуется не перезаписывать молча.

Варианты:

```text
scientist.webp
scientist_002.webp
```

или ошибка при explicit name.

Точная политика фиксируется в storage-документе.

---

# Batch input semantics 📚

`image batch` может поддерживать несколько режимов.

Для v0.1 рекомендуется один простой контракт:

```text
каждый positional prompt file = отдельный Job
```

Например:

```bash
aimedia image batch a.md b.md c.md
```

→ три Jobs.

Не стоит в первой версии добавлять сложные CSV manifests, YAML batch specs и nested combinations без реальной необходимости.

---

# Shared options в batch 🔗

Параметры:

```text
--model
--provider
--resolution
--aspect-ratio
--format
--out
```

применяются ко всем Jobs batch.

---

# Per-job options в batch 🧩

Если позже понадобится разная конфигурация по Job, лучше добавить отдельный manifest format как новую возможность.

Не перегружать positional syntax.

---

# Progress в batch 📊

Human mode может показывать progress.

Например:

```text
[2/5] completed
```

или таблицу статусов.

JSON mode progress не выводит.

---

# Interrupt / Ctrl+C 🛑

При `Ctrl+C`:

- CLI прекращает ожидание;
- сохраняет уже известные remote job IDs;
- не удаляет Job history;
- по возможности корректно завершает локальные async tasks.

Позже пользователь может выполнить:

```bash
aimedia jobs sync
```

---

# `--detach` как будущая опция ⏳

Опционально можно добавить:

```bash
aimedia image generate ... --detach
```

В этом случае CLI:

1. создаёт Job;
2. отправляет request;
3. сохраняет remote job ID;
4. сразу возвращает управление.

Ответ:

```text
Job #481 submitted
```

Это полезно, но не является обязательной частью минимальной v0.1.

---

# Machine-readable capabilities 🤖

AI-агент должен иметь возможность узнать возможности модели без чтения человеческого prose.

Основной контракт:

```bash
aimedia models show seedream-5-pro --json
```

Пример:

```json
{
  "ok": true,
  "data": {
    "id": "seedream-5-pro",
    "family": "image",
    "capabilities": {
      "text_to_image": true,
      "image_to_image": true,
      "multi_reference": true
    },
    "parameters": {
      "resolution": {
        "type": "enum",
        "values": ["1K", "2K", "4K"]
      },
      "aspect_ratio": {
        "type": "enum",
        "values": ["1:1", "16:9", "9:16"]
      }
    }
  }
}
```

---

# Help для одного параметра 🔍

Полезное будущее расширение:

```bash
aimedia models show seedream-5-pro --param resolution
```

Human:

```text
resolution

Type: enum
Allowed:
  1K
  2K
  4K
```

JSON mode возвращает только соответствующую definition.

---

# Version command 🔢

```bash
aimedia version
```

или:

```bash
aimedia --version
```

должна показывать версию CLI.

В JSON mode:

```json
{
  "ok": true,
  "data": {
    "version": "0.1.0"
  }
}
```

---

# CLI naming stability 📌

Имена публичных команд не должны меняться из-за внутренних рефакторингов.

Например изменение:

```text
GenerateImageUseCase
```

на:

```text
CreateImageUseCase
```

не должно менять:

```text
aimedia image generate
```

---

# Deprecated commands 🧓

Если когда-нибудь команда меняется, старый вариант желательно временно поддерживать с warning.

Например:

```text
Deprecated: use `aimedia models show` instead.
```

Для v0.1 механизм deprecation можно не реализовывать, но контракт должен учитывать эту возможность.

---

# Stable vs experimental options 🧪

Если появляется экспериментальная функция, она может быть явно помечена.

Например:

```text
--experimental-foo
```

или через docs.

Не следует превращать экспериментальную опцию сразу в стабильный публичный контракт.

---

# JSON result для `image generate` 📦

Рекомендуемая форма:

```json
{
  "ok": true,
  "data": {
    "job": {
      "id": 481,
      "kind": "image.generate",
      "status": "completed",
      "provider": "polza",
      "model": "seedream-5-pro"
    },
    "cost": {
      "amount": "4.00",
      "currency": "RUB"
    },
    "usage": {
      "output_units": 1
    },
    "artifacts": [
      {
        "kind": "image",
        "path": "D:\\AI-Media\\outputs\\481\\result_001.webp",
        "mime_type": "image/webp"
      }
    ]
  },
  "warnings": []
}
```

---

# JSON result для batch 📦

```json
{
  "ok": false,
  "data": {
    "total": 3,
    "completed": 2,
    "failed": 1,
    "jobs": [
      {
        "id": 101,
        "status": "completed"
      },
      {
        "id": 102,
        "status": "failed",
        "error": {
          "code": "REMOTE_GENERATION_FAILED",
          "message": "..."
        }
      },
      {
        "id": 103,
        "status": "completed"
      }
    ]
  }
}
```

Здесь `ok: false` означает, что batch завершён частично.

---

# JSON и числа 💵

Денежные значения желательно отдавать строкой:

```json
"amount": "4.00"
```

а не floating point.

Обычные технические метрики:

```json
"duration_seconds": 18.4
```

могут быть числами.

---

# JSON timestamps 🕒

Рекомендуемый формат:

```text
ISO 8601
UTC
```

Например:

```json
"created_at": "2026-09-28T08:00:00Z"
```

---

# JSON nullability 🕳️

Поля могут быть:

- отсутствующими;
- либо `null`.

Предпочтительно договориться об одном подходе.

Для публичного contract рекомендуется:

```text
если поле концептуально существует, но значение неизвестно → null
```

Это упрощает агентский парсинг.

---

# Security behavior 🔐

CLI никогда не должна выводить API key.

Ни в:

```text
--verbose
--json
jobs show
error details
logs
```

Authorization headers также не выводятся.

---

# Prompt privacy 🧾

По умолчанию `jobs show` может показывать prompt, потому что это локальная утилита и история является функцией продукта.

Но если позже появится экспорт логов/diagnostics, prompts не должны автоматически утекать туда.

---

# Shell-friendly behavior 🐚

CLI должна корректно возвращать exit code и не требовать interactive prompt там, где команда используется агентом.

Публичные automation-команды должны быть неинтерактивными по умолчанию.

Плохо:

```text
Are you sure? [y/N]
```

в середине обычного agent workflow.

Если подтверждение действительно нужно, должен существовать:

```text
--yes
```

или другая детерминированная форма.

---

# Interactive режимы 🗨️

В v0.1 они не являются приоритетом.

CLI не должна зависеть от TUI wizard для базовых действий.

Все основные сценарии должны быть доступны одной командой с аргументами.

---

# Command discovery 🔎

Пользователь должен иметь возможность выполнить:

```bash
aimedia --help
```

и увидеть основные domains.

Например:

```text
Commands:
  image
  jobs
  models
  providers
  help
  version
```

---

# Автодополнение shell ⌨️

Typer позволяет добавлять shell completion.

Это полезно, но не входит в критический продуктовый контракт v0.1.

---

# Документация и CLI должны совпадать 📖

Markdown docs не должны описывать опции, которых нет в CLI.

И наоборот, публичная стабильная опция должна быть отражена в документации.

Идеально, если часть reference help генерируется из CLI metadata автоматически.

---

# CLI help и Model Registry 🔗

Некоторые значения help не должны дублироваться вручную.

Например список разрешений конкретной модели следует получать из Registry.

Иначе документация быстро устареет.

---

# Команды будущих модулей 🔭

## Audio transcription

```bash
aimedia audio transcribe lecture.mp3 \
  --model whisper-large-v3 \
  --language ru
```

## Speech

```bash
aimedia audio speech \
  --text-file narration.md \
  --voice alloy \
  --format mp3
```

## Text generation

```bash
aimedia text generate \
  --prompt-file task.md \
  --input notes.md \
  --output report.md
```

## Embeddings

```bash
aimedia embedding create \
  --input document.md \
  --model qwen3-embedding-4b
```

Эти команды не обязаны существовать в v0.1, но должны следовать общему CLI grammar.

---

# Общие naming rules для будущих команд 🧭

Используются глаголы:

```text
generate
transcribe
speech
create
show
list
retry
sync
search
```

Следует избегать случайных синонимов:

```text
make
build
produce
run-gen
fetch-job
```

если уже существует согласованный термин.

---

# CLI и idempotency 🔁

Повтор одной и той же команды создаёт новый Job.

CLI не должна автоматически дедуплицировать генерации только потому, что prompt совпал.

Это ожидаемое поведение генеративной системы.

---

# Dry run 🧪

Полезное будущее расширение:

```bash
--dry-run
```

Оно может:

- собрать compiled prompt;
- разрешить модель;
- проверить параметры;
- показать provider mapping;
- не отправлять запрос.

Для первой версии не обязательно, но архитектурно очень полезно.

---

# Debug mode 🐛

В будущем:

```bash
--debug
```

может показывать stack traces и расширенные diagnostics.

Но даже debug mode не должен раскрывать secrets.

---

# CLI contract для partial result 🧩

Если Job завершился частично, human output должен это показать явно.

Пример:

```text
Generation failed after 2 of 4 artifacts were saved.

Saved:
  result_001.webp
  result_002.webp
```

JSON должен содержать:

```text
status = failed
artifacts = [...]
error = ...
```

---

# Устойчивость к provider-specific status 🔄

CLI никогда не должна показывать provider status как единственный источник состояния.

Например provider может вернуть:

```text
pending
processing
queued
rendering
```

CLI нормализует их в внутренние:

```text
submitted
running
```

А raw status можно показать только как diagnostic metadata.

---

# Команда `jobs show` и воспроизводимость 🧬

`jobs show` должна быть достаточной, чтобы понять:

```text
что было отправлено
куда
с какими параметрами
что вернулось
```

Это одна из фундаментальных функций CLI.

---

# Возможная команда `jobs clone` 🧪

Позже может появиться:

```bash
aimedia jobs clone 481
```

которая создаёт новый Job с теми же параметрами, но не запускает его или запускает с override.

Однако v0.1 может обойтись `jobs retry`.

---

# CLI contracts и migrations 🔄

Изменение internal DB schema не должно ломать CLI.

Пользовательский automation работает с CLI contract, а не с таблицами SQLite.

---

# Anti-patterns CLI ☠️

## Не использовать provider payload как CLI

Плохо:

```bash
aimedia image generate \
  --image-resolution 2048 \
  --input-json '{"foo":"bar"}'
```

если это просто прямое отражение одного API.

---

## Не делать один универсальный `run`

Плохо:

```bash
aimedia run --type image --mode generate ...
```

Лучше:

```bash
aimedia image generate
```

---

## Не требовать JSON config для обычного вызова

Плохо:

```bash
aimedia image generate --request request.json
```

как единственный путь.

JSON/YAML manifest может быть дополнительной возможностью позже.

---

## Не смешивать human и machine output

Плохо:

```text
Generation finished!
{"ok": true}
```

в одном stdout.

---

## Не использовать непредсказуемые interactive prompts

Особенно в agent mode.

---

# Критерии готовности CLI v0.1 ✅

CLI-контракт можно считать реализованным, если выполняются сценарии:

### Простая генерация

```bash
aimedia image generate \
  --prompt "..." \
  --model <model>
```

### Prompt file

```bash
aimedia image generate \
  --prompt-file prompt.md \
  --model <model>
```

### Несколько prompt sources

```bash
aimedia image generate \
  --prompt-file base.md \
  --prompt "..." \
  --prompt-file scene.md \
  --model <model>
```

### Reference images

```bash
aimedia image generate \
  --prompt-file prompt.md \
  --image a.png \
  --image b.png \
  --model <model>
```

### Output parameters

```bash
aimedia image generate \
  --prompt-file prompt.md \
  --resolution 2K \
  --aspect-ratio 16:9 \
  --format webp \
  --out ./results \
  --model <model>
```

### Batch

```bash
aimedia image batch prompts/*.md \
  --model <model> \
  --concurrency 4
```

### History

```bash
aimedia jobs recent
aimedia jobs show 481
```

### Retry

```bash
aimedia jobs retry 481
```

### Recovery

```bash
aimedia jobs sync
```

### Model capabilities

```bash
aimedia models show <model>
aimedia models show <model> --json
```

### Documentation

```bash
aimedia help image.generate
aimedia help image.generate --raw
```

### Agent mode

Каждая ключевая команда имеет корректный `--json`.

---

# Архитектурные инварианты CLI 🔒

> [!important]
> **1. CLI — публичный API и не должен отражать внутренние class names.**

> [!important]
> **2. `--json` всегда выдаёт только валидный JSON в stdout.**

> [!important]
> **3. Несколько `--prompt-file` в `image generate` образуют один compiled prompt.**

> [!important]
> **4. Несколько файлов в `image batch` образуют несколько независимых Jobs.**

> [!important]
> **5. Порядок prompt sources и reference images сохраняется.**

> [!important]
> **6. Явно переданный неподдерживаемый параметр не игнорируется молча.**

> [!important]
> **7. `--format` означает финальный локальный формат результата.**

> [!important]
> **8. Retry создаёт новый Job.**

> [!important]
> **9. Model ID в CLI — логический ID registry, а не обязательно provider remote ID.**

> [!important]
> **10. Human и Agent режимы используют один и тот же application result.**

> [!important]
> **11. CLI не раскрывает secrets.**

> [!important]
> **12. Полный help берётся из атомарной документации, а обычный `--help` остаётся кратким.**

---

# Что этот документ намеренно не фиксирует ⏸️

В `04-cli-contract.md` не определяется окончательно:

- точная реализация Typer callbacks;
- структура Python command modules;
- конкретный Rich layout;
- точный JSON Schema файл;
- точные модели первой поставки;
- точный default provider;
- точный output path layout;
- retry/backoff policy;
- polling interval;
- DB schema;
- provider request mapping;
- YAML schema registry;
- shell completion details.

Эти решения находятся в других документах серии.

---

# Связь с другими фундаментальными документами 🔗

```text
01-product-scope.md
    Границы продукта.

02-system-architecture.md
    Архитектурные слои и зависимости.

03-domain-model.md
    Сущности, requests, results, invariants.

04-cli-contract.md
    Публичный CLI API.

05-provider-system.md
    Provider adapters и внешние API.

06-model-registry.md
    Модели, capabilities и параметры.

07-storage-history-costs.md
    История, SQLite, artifacts, cost.

08-job-execution.md
    Execution lifecycle, polling, concurrency, retry.

09-documentation-help.md
    Markdown docs и встроенная справка.
```

---

# Итоговый CLI-контракт 🧩

> [!success]
> CLI строится как стабильный публичный интерфейс поверх application layer.
>
> Основная грамматика — `aimedia <domain> <action>`.
>
> В первой версии ключевыми командами являются `image generate`, `image batch`, `jobs recent/show/retry/sync`, `models list/show` и `help`.
>
> Human mode делает работу удобной человеку, а `--json` предоставляет строгий машинно читаемый контракт для AI-агентов и автоматизации.
>
> Prompt sources объединяются детерминированно, reference images сохраняют порядок, параметры моделей проверяются через Model Registry, а provider-specific детали не просачиваются в пользовательский интерфейс.
>
> Такой CLI остаётся простым для image v0.1 и одновременно задаёт устойчивую грамматику для будущих audio, text, embeddings, search и video модулей.
