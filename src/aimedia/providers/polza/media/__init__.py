"""Polza Media API: чистый mapping доменного image request в тело POST /v1/media.

Публичная точка входа — :func:`build_media_request`. Модуль не выполняет HTTP и
не знает про ответы/ошибки provider: это только преобразование payload
(`docs/plans/05-provider-system.md`, «Provider request mapping»).
"""

from __future__ import annotations

from aimedia.providers.polza.media.request import build_media_request

__all__ = ["build_media_request"]
