---
applyTo: "src/aimedia/providers/polza/**,tests/contracts/test_polza_*.py,tests/security/test_download_auth.py"
name: "PROVIDER.PolzaMedia"
description: "Читай при изменении src/aimedia/providers/polza или tests/contracts/test_polza_*, tests/security/test_download_auth.py: зафиксированный base URL, инжектируемый httpx.AsyncClient, консервативная классификация submit/status/result, инвариант SUBMIT_UNCERTAIN без второго POST, потоковый safety-cap, redaction и реализованная граница CDN-скачивания (только документированный URL, внутренний DNS-пинящий пул, без bearer, DEBUG-журнал транспорта)."
---

# PROVIDER — Polza Media adapter (E06, C09c1/C09c2)

Эта инструкция фиксирует **реализованный** контракт транспорта Polza Media:
submit/status/result на срезе C09c1 и безопасное скачивание remote artifact на
срезе C09c2.
План-источники — `docs/plans/05-provider-system.md` («Transport retry»,
«Error mapping», «Download responsibility»), `docs/plans/08-job-execution.md`
(правило v0.1: неизвестный outcome submit не повторяется автоматически),
`docs/plans/README.md` (E06); принятая развилка —
`docs/plans/decisions/implementation-baseline.md` (D10). Схема запросов/ответов —
`docs/Get Media.txt`, `docs/Post Media.txt` (свидетельство схемы, а не подтверждённая
живая поддержка конкретного model ID).

## Владелец и границы

`src/aimedia/providers/polza/` — инфраструктурный adapter: он зависит от домена,
Registry и HTTP-транспорта (`httpx` в gateway, `httpcore` в download), но обратной
зависимости нет; это проверяет `tests/architecture/test_dependencies.py`.

- `gateway.py` — `PolzaProviderGateway`: `submit` (`POST /v1/media`),
  `get_status`/`fetch_result` (`GET /v1/media/{id}`); реализует доменные порты
  `ProviderGateway` и `PollingProviderGateway`.
- `download.py` — `PolzaArtifactDownloader`: принимает доменную `RemoteArtifact` и
  возвращает ограниченные сырые байты; **сам** строит `httpcore.AsyncConnectionPool`
  над внутренним `DnsPinningBackend` (пул снаружи не инжектируется), не использует
  `httpx.AsyncClient` gateway и не читает окружение.
- `media/` — чистые mappers (`build_media_request`, `serialize_media_request`) и
  нормализация ответов (`decode_media_json`, `normalize_media_*`) без HTTP.
- `__init__.py` намеренно **не** импортирует gateway, чтобы чистый mapper оставался
  доступен без сетевого транспорта.

## GPT MIE: один результат, все настройки и входные референсы (CN-05)

Canonical remote `openai/gpt-5.4-image-2`; MIE выбирается только через fixed
ProviderDto.only=[mie], не model qualifier. Новый запрос передаёт Media
`input.max_images=1`; больше одного результата отложено прямым решением владельца
[CN-05](../docs/plans/release-scope.md). Common DTO max 6 не разрешает новый count>1.
Старый @mie и неподтверждённые qualifiers отклоняются до HTTP; endpoint неизменен.

Registry объявляет все документированные MIE resolutions/ratios, prompt≤5000 и
≤16 входных reference images PNG/JPEG/WebP. Число/размеры/форматы/defaults берутся
из effective definition; неизвестный max reference bytes не выдумывается.
Exact-binding conditional check принадлежит registry.validator: auto (включая
omitted/default auto) только 1K; 1:1 не поддерживается в 4K. OpenAI upstream
extra ratios/quality/seed не приписываются MIE. Existing input snapshot/archive
и mapper кодируют реальные проверенные bytes в ImageInputDto.images base64 objects,
без uploader/storage port или чтения исходника после snapshot.

Pricing — exact Decimal MIE tiers по resolution, не actual billing/другие
upstreams. ProviderDto.max_price.image — ceiling published effective max
(для полного GPT MIE каталога 11), only/noFallback/async=true неизменны.
References являются inputs, не количеством Jobs/outputs. Старые multi-output
Job binding/ref/cost не мигрируют; их GET-only sync и generic multi-artifact
processing сохраняются, но не доказывают новые multi-output submits.
Binding остаётся experimental / NOT_LIVE_VERIFIED до отдельных source CLI proofs.
Регрессии: test_polza_mie_count.py, test_polza_priced_routing.py и public CLI
single/all-settings/multiref/legacy snapshot tests.

## GPT Image 2.5 Sunburst / Flare

Отдельные canonical IDs `openai/gpt-image-2.5-sunburst` и
`openai/gpt-image-2.5-flare` используют тот же fixed MIE routing, async и
`input.max_images=1`. Источники: public catalog и model Markdown
[Sunburst](https://polza.ai/models/openai/gpt-image-2.5-sunburst.md) /
[Flare](https://polza.ai/models/openai/gpt-image-2.5-flare.md).
Четыре дополнительные пропорции 27:16, 16:27, 9:8, 8:9 подтверждены также
[ImageInputDto OpenAPI](https://polza.ai/api/openapi.json); mapper допускает их
только после model-specific validation. Старые YAML не расширяются.
Required defaults явно отправляют 1K/auto; limits/enums/pricing принадлежат
Registry. Marketing quality Flare не добавляет wire-параметр. Поддержка documented,
без live/paid подтверждения; security, uncertain submit и billing остаются прежними.

## Фиксированный финансовый фильтр Media

Для exact remote `qwen/image-2.1`, `google/gemini-3.1-flash-image-preview`,
`openai/gpt-5.4-image-2`, `openai/gpt-image-2.5-sunburst` и
`openai/gpt-image-2.5-flare` mapper добавляет **top-level**
`provider={only:[mie],allow_fallbacks:false,max_price:{image:<integer>}}`.
Источник wire-контракта — [MediaRequestDto / ProviderDto](https://polza.ai/docs/api-reference/media/create.md)
и [Nano guide](https://polza.ai/docs/gaidy/nanobanano-2.md).
Потолок — `int(effective.pricing.published_max.to_integral_value(rounding=ROUND_CEILING))`
из exact Decimal RUB tiers; второй таблицы цен и money float нет. Отсутствующая,
не-RUB или непригодная цена закрывается `InvalidParameterValueError` до HTTP,
с безопасным `details={parameter:pricing}`. Нулевая цена не становится unknown.
Caller provider_options не могут менять fixed rule; generic/synthetic mapping
не меняется. Точный body safety-cap включает routing bytes; gateway отправляет
те же serialized bytes ровно одним POST, без retry.

Unqualified Gemini без provider DTO выбирал бы upstream автоматически: MIE цены
не покрывают token-priced google-ai-studio и прочие upstreams. Media не поддерживает
общие key=value model aliases; Gemini@mie/новые qualifiers не вводятся.
Фильтр API **не** гарантирует actual billing, total Job или бюджет 200 RUB:
фактические usage/cost и unknown reservations учитываются отдельно. Все эти
binding остаются experimental/NOT_LIVE_VERIFIED. Регрессии —
`tests/contracts/test_polza_priced_routing.py`.

## Documented async submit для fixed bindings

В том же exact-binding условии финансового фильтра для пяти remote IDs выше
`build_media_request` добавляет **top-level JSON boolean `async: true`** до
проверки окончательных `serialize_media_request` bytes. Single output (`input.max_images=1` для GPT MIE), references и fixed routing не меняются. Caller `provider_options`
не могут ни выключить, ни переопределить async; generic/synthetic/near-match
mapping остаётся без async. Новых model tables, DTO/config/CLI flags нет.
Источник — `docs/Post Media.txt` (MediaRequestDto async); это documented request,
не подтверждение live async latency/acknowledgement для этих bindings.

Принимается только существующий canonical response: безопасный `id`,
`object: media.generation` и известный `status`. `pending`/`processing` дают exact
opaque ref с operation MEDIA, который application подтверждает в истории до
первого GET; immediate `completed` с пригодным image также сохраняется.
Текст документа «taskId» не разрешает taskId alias: taskId-only (в том числе с
object/status), missing/unsafe id, unknown object/status, malformed JSON и
zero-image completed остаются `SUBMIT_UNCERTAIN`, `retryable=None`, без
придуманного ref/billing или fallback POST. Parser не расширяется по
неподтверждённому live shape. После durable ref локальный timeout сохраняет ref;
restart/sync использует только GET. HTTP timeout и wait deadline не меняются.
Регрессии — `test_polza_priced_routing.py`, `test_polza_gateway.py` и
`tests/integration/test_single_image.py`.

## Инварианты транспорта

`httpx.AsyncClient` инжектируется вызывающей стороной и переиспользуется всеми
вызовами; новый client на вызов или polling не создаётся. Ключ API инжектируется
явно и живёт только в памяти: gateway не читает `.env`, переменные окружения,
CLI-флаги или БД.

Base URL зафиксирован как `https://polza.ai/api/v1` и не берётся из запроса, `ref`
или конфигурации клиента. `follow_redirects=False` задаётся на каждом запросе даже
при `follow_redirects=True` у клиента; редирект отклоняется типизированной ошибкой и
не передаёт `Authorization` на чужой host. `remote_job_id` независимо валидируется
как безопасный односегментный ID и percent-кодируется; endpoint по строке ID не
угадывается (D10).

Тело ответа читается только как raw-поток при `Accept-Encoding: identity`;
не-identity `content-encoding` отклоняется до чтения, а объём ограничен
`max_response_bytes` (safety-cap применяется до `decode_media_json`). Если транспорт
уже отдал буферизованный ответ (`response.is_stream_consumed`), поток повторно не
итерируется: используется готовый буфер с тем же cap. Секреты, сырое тело, URL/query
и текст исключения не попадают в `message`/`details`, traceback или
`__cause__`/`__context__`.

## Консервативный submit 🔒

Оплаченный `POST` не повторяется автоматически: второй submit создаёт новую
генерацию и второе списание. Устойчивое правило: **любой ответ, который не
доказывает непринятие запроса, даёт `SUBMIT_UNCERTAIN` с `retryable=None`,
`details.operation=submit` и фиксированным сообщением без сырого provider-текста.
HTTP 408/5xx могут дополнительно нести только безопасные diagnostics ниже;
это не доказательство непринятия и не разрешение повторного POST.**

`SUBMIT_UNCERTAIN` обязателен для:

- транспортного сбоя (timeout/disconnect) на submit;
- HTTP 408 и 5xx;
- нечитаемого (`content-encoding`) или превысившего `max_response_bytes` тела;
- 2xx без пригодного конверта: неразбираемый JSON, отсутствующий или небезопасный
  `remote_job_id` (включая taskId-only), неизвестный `object`/`status`, `completed`
  без пригодного image.

Ошибка строится **вне** блока `except`, поэтому `__cause__` и `__context__` пусты.
Явные отказы остаются различимыми и не превращаются в `SUBMIT_UNCERTAIN`:
400 → `PROVIDER_HTTP_ERROR`, 401 → `PROVIDER_AUTHENTICATION`,
402 → `PROVIDER_INSUFFICIENT_BALANCE`, 403 → `PROVIDER_FORBIDDEN`,
429 → `PROVIDER_RATE_LIMIT` (rate limit — отказ до обработки, а не неизвестный
исход), 3xx → `PROVIDER_REDIRECT`. Известный терминальный `failed` **с валидным ID**
остаётся `REMOTE_GENERATION_FAILED` и не маскируется под неоднозначность.
`UnknownModelError`, `UnknownProviderError` и ошибки входных файлов поднимаются до
HTTP и submit-неоднозначностью не являются.

## Безопасные diagnostics HTTP-отказа

`gateway._http_error` сохраняет numeric `details.http_status` и читает только
`error.code` из конечного enum `ApiErrorBodyPresenter` в `docs/Post Media.txt`:
BAD_REQUEST, UNAUTHORIZED, api_key_revoked, INSUFFICIENT_BALANCE, FORBIDDEN,
NOT_FOUND, REQUEST_TIMEOUT, CONFLICT, PAYLOAD_TOO_LARGE, TOO_MANY_REQUESTS,
BAD_GATEWAY, SERVICE_UNAVAILABLE, INTERNAL_ERROR. Они идут в `JobError.provider_code`,
не заменяют внутреннюю HTTP-классификацию. Из `error.metadata.reason` разрешён
только документированный пример `noProvidersForModel` → `details.reason`.
Неизвестные/malformed значения и duplicate-key JSON отбрасываются; HTTP-код
не меняется. Нечитаемое/oversized тело сохраняет прежний transport отказ/closure.
Токены, содержащие инжектированный API key, отклоняются даже при совпадении с
allowlist; это относится и к существующему bounded trace ID. Сырые message,
raw/details/headers/body/url/query и provider_name не копируются.

Application повторно ограничивает persist/current CLI diagnostics; trace ID
не сохраняется в Job. Владелец этой data policy —
[APP.single-image-execution](APP.single-image-execution.instructions.md).
Тесты: `tests/contracts/test_polza_error_diagnostics.py`,
`tests/integration/test_single_image.py`, `tests/cli/test_complete_cli.py`.

## GET status/result

`get_status` и `fetch_result` выполняют ровно один `GET` (скрытого busy-polling нет)
и сохраняют свои коды: 408 → `PROVIDER_TIMEOUT`, 5xx → `PROVIDER_UNAVAILABLE` (оба
`retryable=True`), неразбираемое тело или несовпадающий ID →
`PROVIDER_INVALID_RESPONSE`, поток сверх лимита → `PROVIDER_RESPONSE_TOO_LARGE`.
Некорректный или чужой `RemoteJobRef` отклоняется до HTTP
(`PROVIDER_INVALID_REMOTE_REF`). Транспортный timeout на GET не маскируется под
«недостаточно средств».

## CDN и remote artifact — реализовано на C09c2 🔒

Скачивание remote artifact — отдельный срез `download.py`
(`PolzaArtifactDownloader` + `DnsPinningBackend` + приватный `_build_pinned_pool`),
а не «дописанная» к gateway функция. Downloader ничего не пишет на диск, не читает
`.env`/переменные окружения и не определяет MIME/контейнер: фактический формат
проверяет downstream по полученным байтам (граница с
[PROCESSING.ImageArtifacts](PROCESSING.image-artifacts.instructions.md)).

- Bearer Polza **не** уходит на CDN: запрос не добавляет `Authorization`, cookie и
  proxy-заголовки; proxy-переменные окружения не читаются (`proxy=None`), uds не
  используется.
- Одобрен **только документированный** host `s3.polza.ai` (`docs/Get Media.txt`
  подтверждает image URL именно на нём). Любой другой host, **включая `cdn.polza.ai`**,
  отклоняется **до DNS** (fail closed, без wildcard): недокументированный Polza CDN
  не считается поддержанным без подтверждающего документа.
- Поддерживается **только документированная** Polza форма доставки
  (`docs/Get Media.txt`): абсолютный `https` URL одобренного host. Inline
  `base64_data` и `provider_file_id` **не поддерживаются** и закрываются отказом
  (fail closed).
- Разрешены только `https` и порт `443`; запрещены userinfo, fragment, IP-literal,
  управляющие символы, пробел, обратный слэш и scheme-relative форма; длина URL
  ограничена до разбора. Подписанный query допускается только на одобренном origin и
  никогда не попадает в `message`, `details` или traceback.
- Редирект не отслеживается, **включая тот же host**; автоматического повтора нет
  (`retries=0`, без собственного цикла). `Content-Encoding` отклоняется до чтения,
  если **любое** его значение (по всем заголовкам и comma-токенам) не `identity`;
  тело ограничено `max_artifact_bytes` до объединения/декомпрессии, `Content-Type` —
  advisory; некорректный HTTP-ответ CDN типизируется как отказ без сырых байт. Разбор
  всех заголовков идёт внутри guarded close, поэтому ответ закрывается на успехе,
  отказе и отмене даже при враждебном `Content-Length`.
- SSRF закрыт собственным `httpcore.AsyncNetworkBackend`, который downloader строит
  **внутри**: произвольный `AsyncConnectionPool` снаружи не принимается. Каждый
  `connect_tcp` резолвит host **один раз** в пределах **конечного** бюджета (DNS
  ограничен `timeout_seconds`, зависший резолвер даёт типизированный отказ), требует,
  чтобы **все** ответы были глобально-публичными (приватный, смешанный, IPv4-mapped,
  multicast, reserved, CGNAT или loopback ответ закрывает соединение до TCP), и
  передаёт прямому `AnyIOBackend` числовой адрес, а не host. Исходный hostname
  остаётся в URL, поэтому TLS SNI и проверка сертификата выполняются по нему со
  строгим ssl-контекстом по умолчанию. `timeout_seconds` конечен и положителен
  (NaN/inf отклоняются); по умолчанию — `DEFAULT_DOWNLOAD_TIMEOUT_SECONDS`.
- Логирование транспорта: downloader выставляет безопасный уровень логгера
  `httpcore` (WARNING), потому что на DEBUG `httpcore` пишет request target
  (подписанный query) и сырые заголовки ответа (`Location` с подписью) до отказа от
  редиректа. Явно понижать уровень `httpcore.*` нельзя — это вернёт утечку; эта же
  политика скрывает `Authorization` gateway, ходящего через `httpcore`.

## Проверки

```bash
uv run --locked --offline --no-env-file python -m pytest tests/contracts -q
uv run --locked --offline --no-env-file python -m pytest tests/security/test_download_auth.py -q
uv run --locked --offline --no-env-file python -m ruff check .
uv run --locked --offline --no-env-file python -m ruff format --check .
uv run --locked --offline --no-env-file python -m mypy src/aimedia
```

Тесты транспорта используют только `httpx.MockTransport` и детерминированные
двойники резолвера/`AsyncNetworkBackend`; реальные сокеты, `.env`, ключ и live-вызовы
Polza в offline-контуре запрещены (`TEST.OfflineQuality`).
