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
параметры взяты из официального public catalog GET 2026-09-30, без авторизации.
У Gemini каталог даёт 14 refs, Guide — 8: консервативно используем максимум 8.
Неизвестные ограничения остаются null, никаких придуманных seeds/quality/unitParam.

Опубликованные RUB цены и максимумы — в effective views ниже, из Registry.
Они не гарантируют будущую цену. Фактическая стоимость сохраняется только из
provider billing. Локальный WebP не native capability.

{{model:qwen-image-2-1}}

{{model:gemini-3-1-flash-image-preview}}
