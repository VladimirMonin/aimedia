"""Provider adapters aimedia: изоляция внешних API от домена.

Пакет содержит transport- и mapping-код конкретных provider'ов (в v0.1 — Polza).
Он может зависеть от домена и Registry, но сам не является частью домена и не
импортируется из `aimedia.domain`/`aimedia.application`
(`docs/plans/05-provider-system.md`, инвариант 1).
"""

from __future__ import annotations
