"""Типизированные DTO записей Model Registry.

Схема описана в `docs/plans/06-model-registry.md`. Один YAML-документ — одна
логическая модель. Модели Registry неизменяемы и запрещают лишние поля
(`extra="forbid"`), чтобы опечатка или незапланированное расширение схемы не
проходили молча.

Инварианты, важные для последующего effective-resolver (C05b):

- неизвестное ограничение остаётся ``None``, а не превращается в придуманное число;
- ``providers.<name>`` хранит только binding к конкретной модели через provider
  (remote ID и сужение ограничений), но не provider config (ключи, base URL,
  timeout);
- провенанс (`verification`/`verified_at`) подтверждает свежесть данных, но не
  выдаёт наличие YAML-записи за проверенную поддержку модели.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Канонический ID и alias: lowercase-kebab-case (`06-model-registry.md`,
# «Требования к ID»). Одно написание на концепт — точек, слэшей и подчёркиваний нет.
_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

CanonicalId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MetricName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Значения enum/параметров: только скаляры YAML без вложенных структур.
ScalarValue = str | int | float | bool


class Family(StrEnum):
    """Modality логической модели."""

    IMAGE = "image"
    AUDIO = "audio"
    TEXT = "text"
    EMBEDDING = "embedding"
    VIDEO = "video"


class ModelStatus(StrEnum):
    """Жизненный статус записи Registry."""

    ACTIVE = "active"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class ParameterType(StrEnum):
    """Базовые типы параметров Registry."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"


class RegistryModel(BaseModel):
    """Базовая неизменяемая модель вывода Registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _is_canonical_id(value: str) -> bool:
    return _ID_RE.fullmatch(value) is not None


class ParameterSpec(RegistryModel):
    """Описание одного параметра модели.

    `min`/`max`/`default` необязательны: неизвестное ограничение остаётся `None`.
    Для `enum` список `values` обязателен, а `default` обязан входить в него.
    """

    type: ParameterType
    values: tuple[ScalarValue, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None
    max_length: int | None = None
    required: bool = False
    default: ScalarValue | None = None
    title: str | None = None
    description: str | None = None
    docs: str | None = None

    @field_validator("values")
    @classmethod
    def _unique_values(
        cls, values: tuple[ScalarValue, ...] | None
    ) -> tuple[ScalarValue, ...] | None:
        if values is None:
            return None
        if not values:
            raise ValueError("enum-параметр требует непустой список values")
        seen: list[ScalarValue] = []
        for value in values:
            if value in seen:
                raise ValueError(f"дублирующееся значение параметра: {value!r}")
            seen.append(value)
        return values

    @model_validator(mode="after")
    def _check_consistency(self) -> ParameterSpec:
        if self.type is ParameterType.ENUM and self.values is None:
            raise ValueError("enum-параметр требует список values")
        if self.type is not ParameterType.ENUM and self.values is not None:
            raise ValueError("values допустим только для enum-параметра")
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("max параметра меньше min")
        if self.default is not None:
            self._check_default()
        return self

    def _check_default(self) -> None:
        default = self.default
        if self.type is ParameterType.ENUM:
            assert self.values is not None  # гарантировано выше
            if default not in self.values:
                raise ValueError(f"default {default!r} отсутствует в enum values")
            return
        type_ok = {
            ParameterType.STRING: isinstance(default, str),
            ParameterType.INTEGER: isinstance(default, int) and not isinstance(default, bool),
            ParameterType.NUMBER: isinstance(default, (int, float))
            and not isinstance(default, bool),
            ParameterType.BOOLEAN: isinstance(default, bool),
        }[self.type]
        if not type_ok:
            raise ValueError(f"default {default!r} не соответствует типу {self.type.value}")
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            if self.min is not None and default < self.min:
                raise ValueError("default параметра меньше min")
            if self.max is not None and default > self.max:
                raise ValueError("default параметра больше max")


class ParameterOverride(RegistryModel):
    """Сужение параметра для конкретного binding provider.

    Override может только ограничить базовую модель (уже набор значений, более
    узкие границы, заменённый default); решение о слиянии принимает resolver C05b.
    """

    values: tuple[ScalarValue, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None
    default: ScalarValue | None = None


class ProviderBinding(RegistryModel):
    """Binding логической модели к provider.

    Не содержит provider config: ключей, base URL и HTTP timeout здесь быть не
    должно (`06-model-registry.md`, «Provider binding — не provider adapter config»).
    `parameter_map` в v0.1 неактивен (baseline D07), поэтому не входит в схему.
    """

    remote_model_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    parameter_overrides: dict[MetricName, ParameterOverride] = Field(default_factory=dict)


class InputLimit(RegistryModel):
    """Количественные ограничения одного типа входа/выхода."""

    supported: bool | None = None
    required: bool | None = None
    min: int | None = None
    max: int | None = None
    max_chars: int | None = None
    formats: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> InputLimit:
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("max входа/выхода меньше min")
        return self


class CapabilityNode(RegistryModel):
    """Структурированная capability: поддержка плюс количественные границы.

    Единое написание границ — `min`/`max`; неизвестная граница остаётся `None`.
    """

    supported: bool
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> CapabilityNode:
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("max capability меньше min")
        return self


CapabilityValue = bool | CapabilityNode


class Verification(RegistryModel):
    """Провенанс сведений о модели: когда и из какого источника они получены."""

    checked_at: date
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DocsRef(RegistryModel):
    """Ссылка на документацию модели внутри репозитория."""

    overview: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ModelRecord(RegistryModel):
    """Одна логическая модель Registry.

    `model_id` — канонический локальный ID (`06-model-registry.md`, «Канонический
    Model ID»), `providers` — bindings к provider с их remote ID.
    """

    schema_version: int
    model_id: CanonicalId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    family: Family
    status: ModelStatus
    aliases: tuple[CanonicalId, ...] = ()
    capabilities: dict[MetricName, CapabilityValue] = Field(default_factory=dict)
    inputs: dict[MetricName, InputLimit] = Field(default_factory=dict)
    outputs: dict[MetricName, InputLimit] = Field(default_factory=dict)
    parameters: dict[MetricName, ParameterSpec] = Field(default_factory=dict)
    providers: dict[MetricName, ProviderBinding] = Field(default_factory=dict)
    verification: Verification | None = None
    verified_at: date | None = None
    docs: DocsRef | None = None

    @field_validator("model_id")
    @classmethod
    def _valid_model_id(cls, value: str) -> str:
        if not _is_canonical_id(value):
            raise ValueError(f"канонический ID {value!r} не в форме lowercase-kebab-case")
        return value

    @field_validator("aliases")
    @classmethod
    def _valid_aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        seen: list[str] = []
        for alias in aliases:
            if not _is_canonical_id(alias):
                raise ValueError(f"alias {alias!r} не в форме lowercase-kebab-case")
            if alias in seen:
                raise ValueError(f"дублирующийся alias внутри записи: {alias!r}")
            seen.append(alias)
        return aliases

    @model_validator(mode="after")
    def _aliases_exclude_own_id(self) -> ModelRecord:
        if self.model_id in self.aliases:
            raise ValueError("alias совпадает с каноническим ID той же модели")
        return self


__all__ = [
    "CanonicalId",
    "CapabilityNode",
    "CapabilityValue",
    "DocsRef",
    "Family",
    "InputLimit",
    "ModelRecord",
    "ModelStatus",
    "ParameterOverride",
    "ParameterSpec",
    "ParameterType",
    "ProviderBinding",
    "RegistryModel",
    "ScalarValue",
    "Verification",
]
