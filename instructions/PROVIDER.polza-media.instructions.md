---
applyTo: "src/aimedia/providers/polza/**,tests/contracts/test_polza_*.py"
name: "PROVIDER.PolzaMedia"
description: "Читай при изменении src/aimedia/providers/polza или tests/contracts/test_polza_*: зафиксированный base URL, инжектируемый httpx.AsyncClient, консервативная классификация submit/status/result, инвариант SUBMIT_UNCERTAIN без второго POST, потоковый safety-cap, redaction и граница CDN-скачивания."
---

# PROVIDER — Polza Media adapter (E06, C09c1)

Эта инструкция фиксирует **реализованный на C09c1** контракт транспорта Polza Media.
План-источники — `docs/plans/05-provider-system.md` («Transport retry»,
«Error mapping», «Retryable errors»), `docs/plans/08-job-execution.md` (правило
v0.1: неизвестный outcome submit не повторяется автоматически), `docs/plans/README.md`
(E06); принятая развилка — `docs/plans/decisions/implementation-baseline.md` (D10).
Схема запросов/ответов — `docs/Get Media.txt`, `docs/Post Media.txt` (свидетельство
схемы, а не подтверждённая живая поддержка конкретного model ID).

## Владелец и границы

`src/aimedia/providers/polza/` — инфраструктурный adapter: он зависит от домена,
Registry и `httpx`, но обратной зависимости нет; это проверяет
`tests/architecture/test_dependencies.py`.

- `gateway.py` — `PolzaProviderGateway`: `submit` (`POST /v1/media`),
  `get_status`/`fetch_result` (`GET /v1/media/{id}`); реализует доменные порты
  `ProviderGateway` и `PollingProviderGateway`.
- `media/` — чистые mappers (`build_media_request`, `serialize_media_request`) и
  нормализация ответов (`decode_media_json`, `normalize_media_*`) без HTTP.
- `__init__.py` намеренно **не** импортирует gateway, чтобы чистый mapper оставался
  доступен без сетевого транспорта.

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
`details={"operation": "submit"}` и фиксированным сообщением без provider-данных.**

`SUBMIT_UNCERTAIN` обязателен для:

- транспортного сбоя (timeout/disconnect) на submit;
- HTTP 408 и 5xx;
- нечитаемого (`content-encoding`) или превысившего `max_response_bytes` тела;
- 2xx без пригодного конверта: неразбираемый JSON, отсутствующий или небезопасный
  `remote_job_id`, неизвестный `object`/`status`, `completed` без пригодного image.

Ошибка строится **вне** блока `except`, поэтому `__cause__` и `__context__` пусты.
Явные отказы остаются различимыми и не превращаются в `SUBMIT_UNCERTAIN`:
400 → `PROVIDER_HTTP_ERROR`, 401 → `PROVIDER_AUTHENTICATION`,
402 → `PROVIDER_INSUFFICIENT_BALANCE`, 403 → `PROVIDER_FORBIDDEN`,
429 → `PROVIDER_RATE_LIMIT` (rate limit — отказ до обработки, а не неизвестный
исход), 3xx → `PROVIDER_REDIRECT`. Известный терминальный `failed` **с валидным ID**
остаётся `REMOTE_GENERATION_FAILED` и не маскируется под неоднозначность.
`UnknownModelError`, `UnknownProviderError` и ошибки входных файлов поднимаются до
HTTP и submit-неоднозначностью не являются.

## GET status/result

`get_status` и `fetch_result` выполняют ровно один `GET` (скрытого busy-polling нет)
и сохраняют свои коды: 408 → `PROVIDER_TIMEOUT`, 5xx → `PROVIDER_UNAVAILABLE` (оба
`retryable=True`), неразбираемое тело или несовпадающий ID →
`PROVIDER_INVALID_RESPONSE`, поток сверх лимита → `PROVIDER_RESPONSE_TOO_LARGE`.
Некорректный или чужой `RemoteJobRef` отклоняется до HTTP
(`PROVIDER_INVALID_REMOTE_REF`). Транспортный timeout на GET не маскируется под
«недостаточно средств».

## CDN и remote artifact — NOT IMPLEMENTED

Скачивание remote artifact / CDN и SSRF-защита — срез C09c2, **не реализован**:
`RemoteArtifact` остаётся ссылкой. Пока C09c2 нет, bearer не должен уходить на
произвольный CDN-host. Скачивание обязано появиться отдельным срезом со своим
контрактом redirect/SSRF и тестами (`tests/security/test_download_auth.py`), а не
«дописаться» к gateway.

## Проверки

```bash
uv run --locked --offline --no-env-file python -m pytest tests/contracts -q
uv run --locked --offline --no-env-file python -m ruff check .
uv run --locked --offline --no-env-file python -m ruff format --check .
uv run --locked --offline --no-env-file python -m mypy src/aimedia
```

Тесты транспорта используют только `httpx.MockTransport`; сеть, `.env`, ключ и
live-вызовы Polza в offline-контуре запрещены (`TEST.OfflineQuality`).
