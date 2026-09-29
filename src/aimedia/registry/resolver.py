"""Разрешение модели и построение effective definition (E03, C05b).

Resolver — отдельная обязанность от parsing YAML (`06-model-registry.md`,
«Resolver»): он переводит alias в канонический ID и строит effective definition
для выбранного provider как `base model + provider overrides`.

Ключевой инвариант — override может только **сужать** базовую capability
(`06-model-registry.md`, «Принцип override»). Если базовая граница неизвестна
(`None`), она остаётся `None` без override; сам override может объявить
документированную provider-specific границу. Молча выигрывать у базовой модели
или расширять её запрещено: такой binding становится типизированной ошибкой
:class:`~aimedia.registry.errors.InvalidProviderOverrideError`, а не «побеждает».

В v0.1 `ProviderBinding` умеет сужать только `parameters`; отдельных override
для `inputs`/`outputs` (число reference images и т.п.) в схеме нет, поэтому
resolver не выдумывает второй механизм.
"""

from __future__ import annotations

from collections.abc import Iterable

from aimedia.registry.errors import (
    DuplicateAliasError,
    DuplicateModelIdError,
    InvalidProviderOverrideError,
    ModelNotAvailableOnProviderError,
    UnknownModelError,
)
from aimedia.registry.models import (
    EffectiveModelDefinition,
    ModelRecord,
    ParameterOverride,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    ScalarValue,
)

_NUMERIC_TYPES = (ParameterType.INTEGER, ParameterType.NUMBER)


class ModelResolver:
    """Разрешать alias/canonical ID и строить effective definition.

    Resolver принимает уже разобранные записи Registry (offline-данные). Он не
    читает YAML и не ходит в сеть. Для корректности `alias -> canonical ID`
    уникальность ID и alias проверяется при построении: два владельца одного
    alias или один ID на две записи — ошибка, а не «победа последней».
    """

    def __init__(self, records: Iterable[ModelRecord]) -> None:
        ordered = tuple(records)
        self._by_id: dict[str, ModelRecord] = {}
        for record in ordered:
            if record.model_id in self._by_id:
                raise DuplicateModelIdError(record.model_id)
            self._by_id[record.model_id] = record

        self._canonical_by_alias: dict[str, str] = {}
        for record in ordered:
            for alias in record.aliases:
                if alias in self._by_id:
                    raise DuplicateAliasError(
                        alias, conflict=f"alias совпадает с каноническим ID {alias!r}"
                    )
                if alias in self._canonical_by_alias:
                    owner = self._canonical_by_alias[alias]
                    raise DuplicateAliasError(alias, conflict=f"alias уже занят записью {owner!r}")
                self._canonical_by_alias[alias] = record.model_id

    def canonical_id(self, model_id_or_alias: str) -> str:
        """Разрешить canonical ID или alias в канонический ID модели."""
        if model_id_or_alias in self._by_id:
            return model_id_or_alias
        canonical = self._canonical_by_alias.get(model_id_or_alias)
        if canonical is None:
            raise UnknownModelError(model_id_or_alias)
        return canonical

    def get(self, model_id_or_alias: str) -> ModelRecord:
        """Вернуть базовую запись Registry по canonical ID или alias."""
        return self._by_id[self.canonical_id(model_id_or_alias)]

    def resolve(self, model_id_or_alias: str, provider_id: str) -> EffectiveModelDefinition:
        """Построить effective definition для выбранного provider.

        Ошибки: :class:`~aimedia.registry.errors.UnknownModelError` — модели нет
        в Registry;
        :class:`~aimedia.registry.errors.ModelNotAvailableOnProviderError` — у
        модели нет binding к provider;
        :class:`~aimedia.registry.errors.InvalidProviderOverrideError` — override
        не является сужением базовой модели.
        """
        record = self.get(model_id_or_alias)
        binding = self._select_binding(record, provider_id)
        for name in binding.parameter_overrides:
            if name not in record.parameters:
                raise InvalidProviderOverrideError(
                    name, "override ссылается на несуществующий параметр модели"
                )
        parameters = {
            name: _merge_parameter(name, spec, binding.parameter_overrides.get(name))
            for name, spec in record.parameters.items()
        }

        return EffectiveModelDefinition(
            requested_model=model_id_or_alias,
            model_id=record.model_id,
            provider_id=provider_id,
            remote_model_id=binding.remote_model_id,
            name=record.name,
            family=record.family,
            status=record.status,
            aliases=record.aliases,
            capabilities=record.capabilities,
            inputs=record.inputs,
            outputs=record.outputs,
            parameters=parameters,
            verification=record.verification,
            verified_at=record.verified_at,
            docs=record.docs,
        )

    @staticmethod
    def _select_binding(record: ModelRecord, provider_id: str) -> ProviderBinding:
        binding = record.providers.get(provider_id)
        if binding is None:
            raise ModelNotAvailableOnProviderError(
                record.model_id, provider_id, available=sorted(record.providers)
            )
        return binding


def resolve_model(
    records: Iterable[ModelRecord], model_id_or_alias: str, provider_id: str
) -> EffectiveModelDefinition:
    """Удобная обёртка: один раз построить resolver и разрешить модель."""
    return ModelResolver(records).resolve(model_id_or_alias, provider_id)


def _merge_parameter(
    name: str, spec: ParameterSpec, override: ParameterOverride | None
) -> ParameterSpec:
    """Слить базовый ParameterSpec с override, разрешив только сужение."""
    if override is None:
        return spec

    if spec.type is ParameterType.ENUM:
        values = _merged_enum_values(name, spec, override)
    else:
        if override.values is not None:
            raise InvalidProviderOverrideError(name, "values применим только к enum-параметру")
        values = spec.values

    if (override.min is not None or override.max is not None) and spec.type not in _NUMERIC_TYPES:
        raise InvalidProviderOverrideError(
            name, "границы min/max применимы только к числовому параметру"
        )
    low = _merged_min(name, spec.min, override.min)
    high = _merged_max(name, spec.max, override.max)
    if low is not None and high is not None and high < low:
        raise InvalidProviderOverrideError(name, "сужение делает max меньше min")

    default: ScalarValue | None = override.default if override.default is not None else spec.default
    _check_default(name, spec.type, values, low, high, default)

    return ParameterSpec(
        type=spec.type,
        values=values,
        min=low,
        max=high,
        max_length=spec.max_length,
        required=spec.required,
        default=default,
        title=spec.title,
        description=spec.description,
        docs=spec.docs,
    )


def _merged_enum_values(
    name: str, spec: ParameterSpec, override: ParameterOverride
) -> tuple[ScalarValue, ...] | None:
    if override.values is None:
        return spec.values
    # enum-инвариант гарантирует непустой `values` у базовой модели.
    assert spec.values is not None
    if not override.values:
        raise InvalidProviderOverrideError(name, "override оставляет enum без значений")
    if not set(override.values).issubset(set(spec.values)):
        raise InvalidProviderOverrideError(name, "override расширяет набор значений enum")
    return override.values


def _merged_min(
    name: str, base: int | float | None, override: int | float | None
) -> int | float | None:
    if override is None:
        return base
    if base is not None and override < base:
        raise InvalidProviderOverrideError(name, "override расширяет нижнюю границу")
    return override


def _merged_max(
    name: str, base: int | float | None, override: int | float | None
) -> int | float | None:
    if override is None:
        return base
    if base is not None and override > base:
        raise InvalidProviderOverrideError(name, "override расширяет верхнюю границу")
    return override


def _check_default(
    name: str,
    parameter_type: ParameterType,
    values: tuple[ScalarValue, ...] | None,
    low: int | float | None,
    high: int | float | None,
    default: ScalarValue | None,
) -> None:
    """Проверить, что effective default входит в суженный допустимый набор."""
    if default is None:
        return
    if parameter_type is ParameterType.ENUM:
        assert values is not None
        if default not in values:
            raise InvalidProviderOverrideError(
                name, f"default {default!r} вне суженного набора значений"
            )
        return
    if parameter_type in _NUMERIC_TYPES:
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise InvalidProviderOverrideError(name, f"default {default!r} не числового типа")
        if low is not None and default < low:
            raise InvalidProviderOverrideError(name, f"default {default!r} меньше суженного min")
        if high is not None and default > high:
            raise InvalidProviderOverrideError(name, f"default {default!r} больше суженного max")
        return
    if parameter_type is ParameterType.STRING and not isinstance(default, str):
        raise InvalidProviderOverrideError(name, f"default {default!r} не строкового типа")
    if parameter_type is ParameterType.BOOLEAN and not isinstance(default, bool):
        raise InvalidProviderOverrideError(name, f"default {default!r} не булева типа")


__all__ = [
    "ModelResolver",
    "resolve_model",
]
