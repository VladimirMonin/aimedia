"""Packaged atomic Markdown, strict metadata and two data-only directives."""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aimedia.domain.errors import InvalidParameterValueError
from aimedia.registry.builtin import load_builtin_registry
from aimedia.registry.loader import _load_single_document
from aimedia.registry.resolver import ModelResolver
from aimedia.registry.views import build_model_view, render_model_help


class HelpTopic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    topic: str = Field(pattern=r"^[a-z][a-z0-9.-]*$")
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    status: Literal["stable", "experimental", "deprecated", "internal"]
    related: tuple[str, ...] = ()
    markdown: str


class HelpRegistry:
    def __init__(self, *, provider: str = "polza") -> None:
        self.records = load_builtin_registry()
        self.resolver = ModelResolver(self.records)
        self.provider = provider
        self.topics: dict[str, HelpTopic] = {}
        for entry in sorted(files("aimedia.help.data").iterdir(), key=lambda item: item.name):
            if not entry.name.endswith(".md"):
                continue
            text = entry.read_text(encoding="utf-8")
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                raise ValueError("Help front matter missing")
            front, markdown = text[4:].split("\n---\n", 1)
            metadata = _load_single_document(front, path=None)
            topic = HelpTopic.model_validate({**metadata, "markdown": self.resolve(markdown)})
            if topic.topic in self.topics:
                raise ValueError("Duplicate help topic")
            self.topics[topic.topic] = topic
        for topic in self.topics.values():
            if any(related not in self.topics for related in topic.related):
                raise ValueError("Broken related help topic")

    def resolve(self, markdown: str) -> str:
        def replace(match: re.Match[str]) -> str:
            directive = match.group(1)
            if directive == "models":
                return "\n".join(
                    f"- `{record.model_id}` — {record.name} "
                    f"({record.status.value}; live unverified)"
                    for record in self.records
                    if self.provider in record.providers
                )
            if re.fullmatch(r"model:[a-z0-9]+(?:-[a-z0-9]+)*", directive):
                model = directive.removeprefix("model:")
                view = build_model_view(self.resolver.resolve(model, self.provider))
                return "```text\n" + render_model_help(view) + "\n```"
            raise ValueError("Unknown help directive")

        resolved = re.sub(r"\{\{([^{}]+)\}\}", replace, markdown)
        if "{{" in resolved:
            raise ValueError("Unresolved help directive")
        return resolved

    def get(self, topic: str) -> HelpTopic:
        found = self.topics.get(topic)
        if found is None or found.status == "internal":
            raise InvalidParameterValueError("Тема не найдена; используйте aimedia help")
        return found

    def list(self) -> tuple[HelpTopic, ...]:
        return tuple(
            topic for _, topic in sorted(self.topics.items()) if topic.status != "internal"
        )
