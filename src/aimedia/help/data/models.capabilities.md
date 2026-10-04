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

Опубликованные RUB цены и максимумы — в effective views ниже, из Registry;
это тарифы выбранного MIE, не default/всех upstreams. Для встроенных MIE bindings
adapter отправляет top-level `provider.only=[mie]`, `allow_fallbacks=false` и
`max_price.image` — целочисленный потолок опубликованного effective максимума.
Для этих exact bindings также отправляется top-level boolean `async: true`;
это документированный запрос, не live-проверка async acknowledgement/latency.
При canonical id/object/status ссылка сохраняется до polling; taskId-only или
непригодный ответ остаётся SUBMIT_UNCERTAIN без повторного POST.
Без такого DTO unqualified Gemini выбирает upstream автоматически, включая
токенные тарифы, на которые цены MIE не распространяются. Новых model qualifiers
нет. Это API price filter, не гарантия actual billing, будущей цены, total Job
или бюджета 200 RUB. Фактические usage/cost берутся только из provider billing;
неизвестные списания/reservations остаются неизвестными, не нулём.
Каталог не означает live-приёмку всех режимов; async NOT_LIVE_VERIFIED.
Локальный WebP не native capability.

{{model:qwen-image-2-1}}

{{model:gemini-3-1-flash-image-preview}}

## GPT-5.4 Image 2 MIE: один результат и несколько входных изображений

`gpt-5-4-image-2-mie` использует canonical `openai/gpt-5.4-image-2` с fixed MIE
routing (only=[mie], без fallback) и Media `input.max_images=1`.
Несколько выходных images в одном запросе отложены по прямому решению владельца;
несколько `--image` — входные референсы, не output count.

Включены документированные MIE настройки, prompt и references; значения/лимиты/
цены показаны ниже из Registry. Условные ограничения: auto (в том числе default)
только в 1K; 1:1 недоступно в 4K. Seed/quality и пропорции других upstreams не
принимаются. Не alias GPT Image 2.5/Sunburst. Experimental / NOT_LIVE_VERIFIED.

Цена Registry — metadata выбранного MIE, не обещание actual billing.
Без fixed MIE routing другой upstream может иметь токенную цену. Фактическая
стоимость сохраняется только из ответа provider до download.

{{model:gpt-5-4-image-2-mie}}

## GPT Image 2.5 Sunburst и Flare

Отдельные модели, не aliases GPT-5.4 Image 2. Короткие имена: `sunburst`, `flare`;
`flair` — дополнительный CLI alias Flare, не название модели Polza.
Public catalog/model pages проверены 2026-10-04; живые вызовы не выполнялись.
Используются только MIE, один результат и явные defaults из Registry. Для auto
доступно только 1K, для 1:1 недоступно 4K. Quality из обзорного текста Flare не
входит в опубликованный MIE input contract: quality/seed не поддерживаются.

{{model:gpt-image-2-5-sunburst}}

{{model:gpt-image-2-5-flare}}
