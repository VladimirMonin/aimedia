"""Polza provider adapter: HTTP-клиент, mapping и нормализация ошибок.

На C09a реализован только чистый request mapping для media endpoint
(`polza.media`). HTTP-клиент, ошибки и ответы появятся отдельными срезами
(C09b/C09c) и в этом пакете пока отсутствуют (`docs/plans/README.md`, E06).
"""

from __future__ import annotations
