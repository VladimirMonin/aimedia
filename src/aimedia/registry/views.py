"""Публичное представление effective-модели для `models show/list` и справки (E03).

View строится **из той же** :class:`~aimedia.registry.models.EffectiveModelDefinition`,
которая передаётся в :func:`aimedia.registry.validator.validate_model_request`.
Второй таблицы допустимых значений не существует: help, JSON и validation читают
один объект, поэтому изменение provider override согласованно меняет и рендер, и
проверку запроса (`docs/plans/README.md`, E03).

Инварианты представления:

- неизвестное ограничение остаётся ``null``/«not documented», а не превращается в
  придуманное число;
- в JSON попадают только скаляры: enum → строка, кортеж → массив, ``date`` →
  ISO-строка, без Pydantic-метаданных и объектов ``Path``;
- локальный конечный формат (`final_format`, включая WebP из ``processing``) здесь
  **не** объявляется native-возможностью модели: provider output и локальная
  конвертация разделены (решение baseline D08,
  `docs/plans/06-model-registry.md`, «Output format: provider vs local»).

Происхождение сведений различается явно: ``documented`` — запись с источником и
датой проверки, ``unverified`` — запись без провенанса. Статус ``live_verified``
(подтверждение живым вызовом) в схеме Registry v0.1 отсутствует и не выдумывается:
для него нужны отдельные live-evidence, а не наличие YAML-записи
(решение baseline D16).

CLI-команды `models show/list` появятся на E09; этот модуль даёт только
структурированное представление, без Typer/Rich и без обращения к сети.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import StrEnum

from pydantic import Field

from aimedia.registry.models import (
    CapabilityNode,
    CatalogPricing,
    EffectiveModelDefinition,
    InputLimit,
    ModelRecord,
    ParameterSpec,
    ParameterType,
    RegistryModel,
    ScalarValue,
)


class ProvenanceStatus(StrEnum):
    """Различение «есть в Registry» и «подтверждено живым вызовом».

    В v0.1 схема Registry содержит только ``documented``/``unverified``;
    ``live_verified`` потребовал бы отдельного live-evidence, которого схема не
    содержит.
    """

    DOCUMENTED = "documented"
    UNVERIFIED = "unverified"


class CapabilityView(RegistryModel):
    """Структурированная capability с документированными границами (или без них)."""

    supported: bool
    min: int | None = None
    max: int | None = None


class InputLimitView(RegistryModel):
    """Ограничения входа/выхода; неизвестная граница остаётся ``None``."""

    supported: bool | None = None
    required: bool | None = None
    min: int | None = None
    max: int | None = None
    max_chars: int | None = None
    formats: tuple[str, ...] | None = None


class ParameterView(RegistryModel):
    """Публичный параметр модели.

    ``values`` — только документированный набор; ``min``/``max`` равны ``None``,
    если граница неизвестна.
    """

    type: str
    values: tuple[ScalarValue, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None
    max_length: int | None = None
    required: bool = False
    default: ScalarValue | None = None
    title: str | None = None
    description: str | None = None
    docs: str | None = None


class ProvenanceView(RegistryModel):
    """Происхождение сведений: статус, источник и дата проверки."""

    status: ProvenanceStatus
    source: str | None = None
    checked_at: date | None = None
    verified_at: date | None = None


class ModelView(RegistryModel):
    """Effective-модель одного provider в публичной форме (`models show`)."""

    requested_model: str
    id: str
    name: str
    family: str
    status: str
    provider_id: str
    remote_model_id: str
    aliases: tuple[str, ...] = ()
    capabilities: dict[str, bool | CapabilityView] = Field(default_factory=dict)
    inputs: dict[str, InputLimitView] = Field(default_factory=dict)
    outputs: dict[str, InputLimitView] = Field(default_factory=dict)
    parameters: dict[str, ParameterView] = Field(default_factory=dict)
    provenance: ProvenanceView
    docs_overview: str | None = None
    pricing: CatalogPricing | None = None


class ModelSummaryView(RegistryModel):
    """Краткая запись для `models list`: только из Registry, без hardcoded списка."""

    id: str
    name: str
    family: str
    status: str
    aliases: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()


def build_model_view(effective: EffectiveModelDefinition) -> ModelView:
    """Построить публичное представление из effective definition.

    Ровно тот же объект уходит в validation (`validate_model_request`), поэтому
    показанный набор значений и реально проверяемый набор не могут разойтись.
    Порядок параметров сохраняется как в Registry (полезно для help).
    """
    return ModelView(
        requested_model=effective.requested_model,
        id=effective.model_id,
        name=effective.name,
        family=effective.family.value,
        status=effective.status.value,
        provider_id=effective.provider_id,
        remote_model_id=effective.remote_model_id,
        aliases=tuple(effective.aliases),
        capabilities={
            name: _capability_view(value) for name, value in effective.capabilities.items()
        },
        inputs={name: _input_limit_view(value) for name, value in effective.inputs.items()},
        outputs={name: _input_limit_view(value) for name, value in effective.outputs.items()},
        parameters={name: _parameter_view(spec) for name, spec in effective.parameters.items()},
        provenance=_provenance_view(effective),
        docs_overview=effective.docs.overview if effective.docs is not None else None,
        pricing=effective.pricing,
    )


def build_model_list(records: Iterable[ModelRecord]) -> tuple[ModelSummaryView, ...]:
    """Построить стабильно упорядоченный список моделей Registry.

    `models list` не должен иметь отдельного hardcoded списка
    (`06-model-registry.md`, «`models list` и Registry»): сводки выводятся из
    загруженных записей и сортируются по каноническому ID.
    """
    summaries = [
        ModelSummaryView(
            id=record.model_id,
            name=record.name,
            family=record.family.value,
            status=record.status.value,
            aliases=tuple(record.aliases),
            providers=tuple(sorted(record.providers)),
        )
        for record in records
    ]
    return tuple(sorted(summaries, key=lambda summary: summary.id))


def render_model_help(view: ModelView) -> str:
    """Отрендерить human-readable справку по той же view, что уходит в JSON.

    Неизвестные ограничения печатаются как «not documented», а локальный конечный
    формат здесь не появляется: help не выдаёт локальную обработку за native
    возможность модели.
    """
    lines = [
        f"{view.name} ({view.id})",
        f"Family: {view.family}",
        f"Status: {view.status}",
        f"Provider: {view.provider_id}",
        f"Remote model: {view.remote_model_id}",
    ]
    if view.aliases:
        lines.append("Aliases: " + ", ".join(view.aliases))

    lines += ["", "Capabilities:"]
    if view.capabilities:
        lines += [
            f"  {name}: {_describe_capability(capability)}"
            for name, capability in view.capabilities.items()
        ]
    else:
        lines.append("  (none documented)")

    lines += ["", "Parameters:"]
    if view.parameters:
        lines += [
            f"  {name}: {_describe_parameter(spec)}" for name, spec in view.parameters.items()
        ]
    else:
        lines.append("  (none documented)")

    if view.inputs:
        lines += [""]
        lines += [f"Input {name}: {_describe_limit(limit)}" for name, limit in view.inputs.items()]
    if view.outputs:
        lines += [""]
        lines += [
            f"Output {name}: {_describe_limit(limit)}" for name, limit in view.outputs.items()
        ]

    if view.pricing is not None:
        lines += ["", "Published catalog pricing (not actual billing/future guarantee):"]
        lines += [
            f"  {resolution}: {amount} {view.pricing.currency}"
            for resolution, amount in view.pricing.by_resolution.items()
        ]
        lines.append(f"  Published maximum: {view.pricing.published_max} {view.pricing.currency}")
        lines.append(f"  Unit parameter: {view.pricing.unit_parameter or 'not published'}")
    provenance = view.provenance
    lines += ["", f"Provenance: {provenance.status.value}"]
    if provenance.source is not None:
        lines.append(f"  Source: {provenance.source}")
    if provenance.checked_at is not None:
        lines.append(f"  Checked at: {provenance.checked_at.isoformat()}")
    if view.docs_overview is not None:
        lines.append(f"Docs: {view.docs_overview}")
    return "\n".join(lines)


def _capability_view(value: bool | CapabilityNode) -> bool | CapabilityView:
    if isinstance(value, bool):
        return value
    return CapabilityView(supported=value.supported, min=value.min, max=value.max)


def _input_limit_view(limit: InputLimit) -> InputLimitView:
    return InputLimitView(
        supported=limit.supported,
        required=limit.required,
        min=limit.min,
        max=limit.max,
        max_chars=limit.max_chars,
        formats=limit.formats,
    )


def _parameter_view(spec: ParameterSpec) -> ParameterView:
    return ParameterView(
        type=spec.type.value,
        values=spec.values,
        min=spec.min,
        max=spec.max,
        max_length=spec.max_length,
        required=spec.required,
        default=spec.default,
        title=spec.title,
        description=spec.description,
        docs=spec.docs,
    )


def _provenance_view(effective: EffectiveModelDefinition) -> ProvenanceView:
    verification = effective.verification
    status = (
        ProvenanceStatus.DOCUMENTED if verification is not None else ProvenanceStatus.UNVERIFIED
    )
    return ProvenanceView(
        status=status,
        source=verification.source if verification is not None else None,
        checked_at=verification.checked_at if verification is not None else None,
        verified_at=effective.verified_at,
    )


def _describe_capability(value: bool | CapabilityView) -> str:
    if isinstance(value, bool):
        return "supported" if value else "not supported"
    text = "supported" if value.supported else "not supported"
    bounds = _format_bounds(value.min, value.max)
    return f"{text} ({bounds})" if bounds else text


def _describe_parameter(spec: ParameterView) -> str:
    facts = [spec.type]
    if spec.values is not None:
        facts.append("values " + ", ".join(str(value) for value in spec.values))
    if spec.min is not None or spec.max is not None:
        facts.append(_format_bounds(spec.min, spec.max))
    elif spec.type in (ParameterType.INTEGER.value, ParameterType.NUMBER.value):
        facts.append("no documented bound")
    if spec.max_length is not None:
        facts.append(f"max length {spec.max_length}")
    if spec.required:
        facts.append("required")
    if spec.default is not None:
        facts.append(f"default {spec.default}")
    if spec.description is not None:
        facts.append(spec.description)
    return "; ".join(facts)


def _describe_limit(limit: InputLimitView) -> str:
    facts: list[str] = []
    if limit.supported is not None:
        facts.append("supported" if limit.supported else "not supported")
    if limit.required is not None:
        facts.append("required" if limit.required else "optional")
    bounds = _format_bounds(limit.min, limit.max)
    if bounds:
        facts.append(bounds)
    if limit.max_chars is not None:
        facts.append(f"max chars {limit.max_chars}")
    if limit.formats is not None:
        facts.append("formats " + ", ".join(limit.formats))
    return "; ".join(facts) if facts else "not documented"


def _format_bounds(low: int | float | None, high: int | float | None) -> str:
    """Описать границы, не подставляя число вместо неизвестной границы."""
    if low is not None and high is not None:
        return f"min {low}, max {high}"
    if low is not None:
        return f"min {low}, max not documented"
    if high is not None:
        return f"min not documented, max {high}"
    return "no documented bound"


__all__ = [
    "CapabilityView",
    "InputLimitView",
    "ModelSummaryView",
    "ModelView",
    "ParameterView",
    "ProvenanceStatus",
    "ProvenanceView",
    "build_model_list",
    "build_model_view",
    "render_model_help",
]
