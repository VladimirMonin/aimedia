"""Компиляция prompt из упорядоченных источников."""

from __future__ import annotations

from aimedia.application.prompts.compile import (
    PROMPT_SOURCE_SEPARATOR,
    PromptCompiler,
    PromptPreparation,
    PromptSourceRequest,
    file_source,
    inline_source,
)

__all__ = [
    "PROMPT_SOURCE_SEPARATOR",
    "PromptCompiler",
    "PromptPreparation",
    "PromptSourceRequest",
    "file_source",
    "inline_source",
]
