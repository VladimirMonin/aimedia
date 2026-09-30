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
