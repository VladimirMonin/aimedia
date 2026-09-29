"""Polza Media API: чистые mappers доменного image request ↔ ответов Polza.

Публичные точки входа — :func:`build_media_request` (тело `POST /v1/media`) и
нормализация ответов :func:`normalize_media_submission`,
:func:`normalize_media_status`, :func:`normalize_media_result` с декодером
:func:`decode_media_json`. Модули не выполняют HTTP и не знают про auth/загрузку
(они относятся к C09c): это только преобразование payload и ответов
(`docs/plans/05-provider-system.md`, «Provider request mapping» и
«Provider response mapping»).
"""

from __future__ import annotations

from aimedia.providers.polza.media.request import build_media_request
from aimedia.providers.polza.media.response import (
    POLZA_PROVIDER_ID,
    decode_media_json,
    normalize_media_result,
    normalize_media_status,
    normalize_media_submission,
)

__all__ = [
    "POLZA_PROVIDER_ID",
    "build_media_request",
    "decode_media_json",
    "normalize_media_result",
    "normalize_media_status",
    "normalize_media_submission",
]
