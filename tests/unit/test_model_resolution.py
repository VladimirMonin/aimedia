"""Resolution alias -> canonical ID и построение effective definition (E03, C05b).

Проверяемые утверждения этапа E03 (`docs/plans/README.md`): alias разрешается в
canonical ID; provider override действительно сужает набор значений, а не
расширяет его; неизвестное ограничение остаётся `None`; missing model/provider
дают явные типизированные ошибки. Все записи здесь синтетические — реальные API
ID и лимиты не выдумываются.
"""

from __future__ import annotations

import pytest

from aimedia.registry import (
    DuplicateAliasError,
    EffectiveModelDefinition,
    InvalidProviderOverrideError,
    ModelNotAvailableOnProviderError,
    ModelResolver,
    ModelStatus,
    ParameterOverride,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    UnknownModelError,
    resolve_model,
)
from aimedia.registry.models import ModelRecord


def _binding(
    remote_model_id: str,
    overrides: dict[str, ParameterOverride] | None = None,
) -> ProviderBinding:
    return ProviderBinding(remote_model_id=remote_model_id, parameter_overrides=overrides or {})


def _resolution_enum() -> ParameterSpec:
    return ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K", "4K"), default="2K")


def _record(
    *,
    model_id: str = "synthetic-image",
    aliases: tuple[str, ...] = (),
    parameters: dict[str, ParameterSpec] | None = None,
    providers: dict[str, ProviderBinding] | None = None,
) -> ModelRecord:
    return ModelRecord(
        schema_version=1,
        model_id=model_id,
        name=f"Synthetic {model_id}",
        family="image",
        status=ModelStatus.ACTIVE,
        aliases=aliases,
        parameters=parameters if parameters is not None else {"resolution": _resolution_enum()},
        providers=providers if providers is not None else {"polza": _binding("synthetic/remote")},
    )


def test_alias_resolves_to_canonical_id_and_record() -> None:
    """Alias разрешается в канонический ID и базовую запись."""
    resolver = ModelResolver([_record(model_id="synthetic-image", aliases=("syn-img",))])

    assert resolver.canonical_id("syn-img") == "synthetic-image"
    assert resolver.canonical_id("synthetic-image") == "synthetic-image"
    assert resolver.get("syn-img").model_id == "synthetic-image"


def test_unknown_model_raises_typed_error() -> None:
    """Неизвестный ID/alias — явная типизированная ошибка Registry."""
    resolver = ModelResolver([_record(model_id="synthetic-image")])
    with pytest.raises(UnknownModelError) as excinfo:
        resolver.canonical_id("synthetic-absent")
    assert excinfo.value.model_id_or_alias == "synthetic-absent"
    assert excinfo.value.code == "UNKNOWN_MODEL"


def test_unknown_provider_binding_raises_typed_error() -> None:
    """Модель без binding к выбранному provider не разрешается молча."""
    resolver = ModelResolver([_record(model_id="synthetic-image")])
    with pytest.raises(ModelNotAvailableOnProviderError) as excinfo:
        resolver.resolve("synthetic-image", "direct")
    assert excinfo.value.model_id == "synthetic-image"
    assert excinfo.value.provider_id == "direct"
    assert excinfo.value.available == ("polza",)
    assert excinfo.value.code == "MODEL_NOT_AVAILABLE_ON_PROVIDER"


def test_resolve_by_alias_keeps_requested_and_canonical_ids() -> None:
    """Effective definition несёт запрошенное значение и канонический ID."""
    resolver = ModelResolver([_record(model_id="synthetic-image", aliases=("syn-img",))])
    effective = resolver.resolve("syn-img", "polza")

    assert isinstance(effective, EffectiveModelDefinition)
    assert effective.requested_model == "syn-img"
    assert effective.model_id == "synthetic-image"
    assert effective.provider_id == "polza"
    assert effective.remote_model_id == "synthetic/remote"


def test_enum_override_narrows_values() -> None:
    """Override действительно сужает набор значений enum."""
    binding = _binding(
        "synthetic/remote",
        {"resolution": ParameterOverride(values=("1K", "2K"), default="1K")},
    )
    resolver = ModelResolver([_record(providers={"polza": binding})])
    effective = resolver.resolve("synthetic-image", "polza")

    resolution = effective.parameters["resolution"]
    assert resolution.values == ("1K", "2K")
    assert resolution.default == "1K"


def test_enum_override_cannot_widen_values() -> None:
    """Override, расширяющий набор значений, отклоняется, а не «побеждает»."""
    binding = _binding(
        "synthetic/remote",
        {"resolution": ParameterOverride(values=("1K", "8K"))},
    )
    resolver = ModelResolver([_record(providers={"polza": binding})])
    with pytest.raises(InvalidProviderOverrideError) as excinfo:
        resolver.resolve("synthetic-image", "polza")
    assert excinfo.value.parameter == "resolution"
    assert excinfo.value.code == "INVALID_PROVIDER_OVERRIDE"


def test_empty_enum_override_without_default_raises_typed_error() -> None:
    spec = ParameterSpec(type=ParameterType.ENUM, values=("1K", "2K"))
    binding = _binding("synthetic/remote", {"resolution": ParameterOverride(values=())})
    resolver = ModelResolver(
        [_record(parameters={"resolution": spec}, providers={"polza": binding})]
    )
    with pytest.raises(InvalidProviderOverrideError) as excinfo:
        resolver.resolve("synthetic-image", "polza")
    assert excinfo.value.parameter == "resolution"
    assert excinfo.value.code == "INVALID_PROVIDER_OVERRIDE"
    assert "без значений" in excinfo.value.reason


def test_override_default_outside_narrowed_set_is_rejected() -> None:
    """Default override вне суженного набора — несовместимая конфигурация."""
    binding = _binding(
        "synthetic/remote",
        {"resolution": ParameterOverride(values=("1K",), default="2K")},
    )
    resolver = ModelResolver([_record(providers={"polza": binding})])
    with pytest.raises(InvalidProviderOverrideError) as excinfo:
        resolver.resolve("synthetic-image", "polza")
    assert excinfo.value.parameter == "resolution"


def test_base_default_outside_narrowed_set_is_rejected() -> None:
    """Базовый default, не входящий в суженный набор, — ошибка, а не тихий откат.

    Без явного override default базовая модель обещает `2K`; provider с
    сужением до `1K` делает этот default невалидным, и resolver обязан сказать
    об этом, а не подставить другое значение.
    """
    binding = _binding("synthetic/remote", {"resolution": ParameterOverride(values=("1K",))})
    resolver = ModelResolver([_record(providers={"polza": binding})])
    with pytest.raises(InvalidProviderOverrideError):
        resolver.resolve("synthetic-image", "polza")


def test_override_numeric_bounds_narrow() -> None:
    """Числовые границы сужаются, а неизвестная граница остаётся `None`."""
    spec = ParameterSpec(type=ParameterType.INTEGER, min=0, max=100)
    binding = _binding("synthetic/remote", {"steps": ParameterOverride(max=50)})
    record = _record(parameters={"steps": spec}, providers={"polza": binding})
    effective = ModelResolver([record]).resolve("synthetic-image", "polza")

    steps = effective.parameters["steps"]
    assert steps.min == 0
    assert steps.max == 50


def test_override_numeric_bound_cannot_widen() -> None:
    """Расширяющая нижняя/верхняя граница отклоняется."""
    spec = ParameterSpec(type=ParameterType.INTEGER, min=0, max=100)
    for override in (ParameterOverride(min=-1), ParameterOverride(max=200)):
        binding = _binding("synthetic/remote", {"steps": override})
        record = _record(parameters={"steps": spec}, providers={"polza": binding})
        with pytest.raises(InvalidProviderOverrideError):
            ModelResolver([record]).resolve("synthetic-image", "polza")


def test_unknown_bound_stays_none_without_override() -> None:
    """Неизвестное ограничение не превращается в придуманное число."""
    spec = ParameterSpec(type=ParameterType.INTEGER)
    record = _record(parameters={"steps": spec}, providers={"polza": _binding("synthetic/r")})
    effective = ModelResolver([record]).resolve("synthetic-image", "polza")

    steps = effective.parameters["steps"]
    assert steps.min is None
    assert steps.max is None


def test_override_can_declare_documented_bound_when_base_unknown() -> None:
    """Override может объявить документированную границу, если база неизвестна."""
    spec = ParameterSpec(type=ParameterType.INTEGER)
    binding = _binding("synthetic/remote", {"steps": ParameterOverride(min=1, max=5)})
    record = _record(parameters={"steps": spec}, providers={"polza": binding})
    effective = ModelResolver([record]).resolve("synthetic-image", "polza")

    steps = effective.parameters["steps"]
    assert steps.min == 1
    assert steps.max == 5


def test_override_unknown_parameter_is_rejected() -> None:
    """Override несуществующего параметра — явная ошибка схемы."""
    binding = _binding("synthetic/remote", {"absent": ParameterOverride(max=1)})
    resolver = ModelResolver([_record(providers={"polza": binding})])
    with pytest.raises(InvalidProviderOverrideError) as excinfo:
        resolver.resolve("synthetic-image", "polza")
    assert excinfo.value.parameter == "absent"


def test_enum_values_override_on_non_enum_is_rejected() -> None:
    """`values` в override числового параметра неприменим."""
    spec = ParameterSpec(type=ParameterType.INTEGER, min=0, max=10)
    binding = _binding("synthetic/remote", {"steps": ParameterOverride(values=(1, 2))})
    record = _record(parameters={"steps": spec}, providers={"polza": binding})
    with pytest.raises(InvalidProviderOverrideError):
        ModelResolver([record]).resolve("synthetic-image", "polza")


def test_duplicate_ids_and_aliases_are_rejected_at_construction() -> None:
    """Resolver не допускает двух владельцев ID/alias."""
    with pytest.raises(DuplicateAliasError):
        ModelResolver(
            [
                _record(model_id="synthetic-a", aliases=("shared",)),
                _record(model_id="synthetic-b", aliases=("shared",)),
            ]
        )


def test_resolve_model_convenience_wrapper() -> None:
    """Обёртка `resolve_model` строит resolver и разрешает модель."""
    effective = resolve_model([_record(model_id="synthetic-image")], "synthetic-image", "polza")
    assert effective.model_id == "synthetic-image"
    assert effective.provider_id == "polza"
