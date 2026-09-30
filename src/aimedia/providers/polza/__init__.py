"""Polza provider adapter: чистые mappers, HTTP-шлюз и нормализация ошибок.

`polza.media` содержит чистые mappers без транспорта. `polza.gateway`
(`PolzaProviderGateway`) добавляет HTTP-транспорт submit/status/result поверх
инжектированного `httpx.AsyncClient`. Пакет намеренно не импортирует gateway в
`__init__`, чтобы чистый media-mapper оставался доступен без сетевого транспорта.
"""

from __future__ import annotations
