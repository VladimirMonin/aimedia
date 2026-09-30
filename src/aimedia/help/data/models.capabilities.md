---
topic: models.capabilities
title: Модели и ограничения
summary: Единый effective Registry для validation/help/JSON
status: experimental
related: [image.generate, image.references, getting-started]
---
# Модели

```text
aimedia models list --json
aimedia models show qwen-image-2-1 --json
```

{{models}}

Документированный каталог ≠ live-проверка. Все встроенные записи experimental;
обычный запуск требует `--allow-experimental` и явный выбор модели. Remote IDs и
параметры Qwen/Gemini взяты из официального public catalog GET 2026-09-30, без авторизации.
MIE count binding подтверждён публичными guide/model Markdown, не live и не MCP catalog.
У Gemini каталог даёт 14 refs, Guide — 8: консервативно используем максимум 8.
Неизвестные ограничения остаются null, никаких придуманных seeds/quality/unitParam.

Опубликованные RUB цены и максимумы — в effective views ниже, из Registry.
Они не гарантируют будущую цену. Фактическая стоимость сохраняется только из
provider billing. Локальный WebP не native capability.

{{model:qwen-image-2-1}}

{{model:gemini-3-1-flash-image-preview}}

## GPT-5.4 Image 2 MIE: count subset

`gpt-5-4-image-2-mie` использует только точный `openai/gpt-5.4-image-2@mie`:
логический `max_images` передаётся как `input.n`, один Job и один платный POST.
Это text-only / 1K subset. Reference URL и более высокие resolutions документированы
API, но не проверены и не включены здесь; это не заявление об отсутствии поддержки
у provider. Не alias GPT Image 2.5/Sunburst. NOT_LIVE_VERIFIED.

Цена Registry относится к одному изображению MIE в включённом режиме, не к total
Job. Неквалифицированный/default-openai маршрут имеет токенную цену: ставки MIE
не являются его верхней ценой. Catalog unitParam не опубликован и не выдуман.
Фактический total берётся только из billing provider до download.

{{model:gpt-5-4-image-2-mie}}
