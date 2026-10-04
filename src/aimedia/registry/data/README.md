# Production image catalog

Только официальные metadata/bindings public Polza catalog GET от 2026-09-30:
`qwen/image-2.1`, `google/gemini-3.1-flash-image-preview`, endpoint `/api/v1/media`.
Исходное offline evidence — `artifacts/metadata/polza-catalog-{qwen,gemini}.json`
(HTTP 200, no auth, paid POST 0; evidence не включается в пакет).

Все записи **experimental / live unverified**, требуют явного выбора и
`--allow-experimental`. `verification` означает документированный источник, не
проверенную живую генерацию. Gemini refs ≤8 по консервативной границе Guide,
несмотря на catalog 14. Unit parameter не опубликован: не выдумывается.
Pricing — точные опубликованные RUB tiers, не гарантия будущего тарифа и не billing.

GPT-5.4 Image 2 MIE добавлен по отдельному model guide. Каталог от 2026-10-04
также подтверждает `openai/gpt-image-2.5-sunburst` и `openai/gpt-image-2.5-flare`:
[Sunburst](https://polza.ai/models/openai/gpt-image-2.5-sunburst.md),
[Flare](https://polza.ai/models/openai/gpt-image-2.5-flare.md),
[public catalog](https://polza.ai/api/v1/models/catalog?search=gpt&type=image).
Их canonical CLI IDs — `gpt-image-2-5-sunburst` / `gpt-image-2-5-flare`, короткие
aliases — `sunburst` / `flare` (`flair` также принимается). Это отдельные модели.
Технические параметры берутся из catalog/input schema, не обзорного marketing text:
prompt≤20000, refs≤16, MIE RUB4/7/11; required defaults явно передаются как 1K/auto.
Generic quality из описания Flare не подтверждает MIE quality. Новые записи
documented/experimental, без live-приёмки.
