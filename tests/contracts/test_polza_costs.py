"""Контрактные тесты нормализации usage/cost ответов Polza Media (E06, C09b).

Проверяется точное преобразование `usage` из `docs/Get Media.txt`
(`MediaUsagePresenter`) в `Usage`/`Cost`: aliases `cost_rub`/`cost` не удваиваются,
ноль остаётся известным нулём, отсутствие цены — `None`, дробные деньги не
проходят через `float`, а `usage.raw` сохраняет исходную провенансность.

Все fixtures синтетические; живой API и каталожные цены здесь не подтверждаются.
Offline-guard из `tests/conftest.py` блокирует внешнюю сеть.
"""

from __future__ import annotations

import builtins
import json
import traceback
from decimal import Decimal

import pytest

from aimedia.domain import Cost, ProviderError, Usage
from aimedia.providers.polza.media import decode_media_json, normalize_media_result

IMAGE_URL = "https://cdn.example.invalid/synthetic.jpg"


def _payload(usage: object = None, *, include_usage: bool = True) -> dict[str, object]:
    """Completed-конверт с синтетическим usage."""
    payload: dict[str, object] = {
        "id": "aig_synthetic",
        "object": "media.generation",
        "status": "completed",
        "created": 1703001244,
        "model": "synthetic/model-id",
        "data": {"url": IMAGE_URL},
    }
    if include_usage:
        payload["usage"] = usage
    return payload


def _cost(usage: object) -> Cost | None:
    result = normalize_media_result(_payload(usage))
    return result.cost


def test_cost_rub_preferred_over_alias_even_at_zero() -> None:
    """`cost_rub=0` не заменяется alias `cost` через `or`."""
    cost = _cost({"cost_rub": 0, "cost": 5})
    assert cost is not None
    assert cost.amount == Decimal("0")
    assert cost.currency == "RUB"
    assert cost.is_zero is True


def test_alias_cost_used_when_cost_rub_absent() -> None:
    cost = _cost({"cost": "1.25"})
    assert cost is not None
    assert cost.amount == Decimal("1.25")
    assert cost.currency == "RUB"


def test_alias_null_falls_through_to_cost() -> None:
    """`cost_rub: null` — отсутствие значения, а не ноль: берётся alias `cost`."""
    cost = _cost({"cost_rub": None, "cost": "2.50"})
    assert cost is not None
    assert cost.amount == Decimal("2.50")


def test_no_cost_fields_is_unknown_none() -> None:
    """Отсутствие обоих aliases — `None`, а не выдуманный ноль или каталожная цена."""
    assert _cost({}) is None
    assert _cost({"input_units": 1, "output_units": 1}) is None
    assert _cost(None) is None


def test_absent_usage_is_unknown_cost() -> None:
    result = normalize_media_result(_payload(include_usage=False))
    assert result.usage is None
    assert result.cost is None


def test_decimal_string_is_exact_not_float() -> None:
    """Дробная строка сохраняется без двоичной дроби."""
    cost = _cost({"cost_rub": "0.1"})
    assert cost is not None
    assert cost.amount == Decimal("0.1")
    assert str(cost.amount) == "0.1"


def test_decimal_exponent_is_preserved() -> None:
    """`Decimal("5.00")` не нормализуется в другой масштаб."""
    cost = _cost({"cost_rub": Decimal("5.00")})
    assert cost is not None
    assert cost.amount == Decimal("5.00")
    assert str(cost.amount) == "5.00"


@pytest.mark.parametrize(
    "value",
    [-1, "-0.01", Decimal("-5"), 1.5, float("nan"), float("inf")],
    ids=["int-negative", "str-negative", "decimal-negative", "float", "nan", "inf"],
)
def test_invalid_cost_values_fail_closed(value: object) -> None:
    """Float, nonfinite и отрицательные деньги — безопасный отказ, не цена."""
    with pytest.raises(ProviderError) as excinfo:
        _cost({"cost_rub": value})
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "invalid_cost"}


@pytest.mark.parametrize(
    "value",
    [True, False, "abc", {"amount": 1}],
    ids=["bool-true", "bool-false", "str", "dict"],
)
def test_non_numeric_cost_fails_closed(value: object) -> None:
    with pytest.raises(ProviderError) as excinfo:
        _cost({"cost_rub": value})
    assert excinfo.value.error.details == {"reason": "invalid_cost"}


def test_malformed_preferred_alias_does_not_fall_back() -> None:
    """Malformed `cost_rub` не маскируется переходом к alias `cost`."""
    with pytest.raises(ProviderError):
        _cost({"cost_rub": "not-a-number", "cost": "5"})


@pytest.mark.parametrize(
    "value",
    [Decimal("1e999999999"), "1e-999999999", Decimal("0e-999999999")],
    ids=["huge-integer", "huge-fraction", "zero-with-huge-scale"],
)
def test_cost_fixed_format_overflow_rejected_without_expansion(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Даже конечная сумма не должна заставлять domain выделять гигантскую строку."""
    original_format = builtins.format

    def no_decimal_expansion(candidate: object, spec: str = "") -> str:
        if isinstance(candidate, Decimal) and spec == "f":
            raise AssertionError("cost expanded before safety check")
        return original_format(candidate, spec)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "format", no_decimal_expansion)
        with pytest.raises(ProviderError) as excinfo:
            _cost({"cost_rub": value, "cost": "1"})
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "invalid_cost"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert str(value) not in "".join(traceback.format_exception(excinfo.value))


def test_cost_fixed_format_cap_accepts_boundary_and_safe_normal_values() -> None:
    """4096 fixed chars: локальная граница записи, не цена Polza/модели."""
    for amount in ("0.01", "12345.00", "1e4095", "1e-4094", "0e-4094", "-0e-4093"):
        cost = _cost({"cost": amount})
        assert cost is not None
        assert cost.amount == Decimal(amount)
        assert len(format(cost.amount, "f")) <= 4096
    for amount in ("1e4096", "1e-4095", "0e-4095", "-0e-4094"):
        with pytest.raises(ProviderError) as excinfo:
            _cost({"cost": amount})
        assert excinfo.value.error.details == {"reason": "invalid_cost"}


def test_cost_size_check_respects_alias_precedence_and_raw_usage() -> None:
    usage = {"cost_rub": "1e3", "cost": "1e999999999"}
    result = normalize_media_result(_payload(usage))
    assert result.cost == Cost(amount=Decimal("1e3"), currency="RUB")
    assert result.usage is not None
    assert result.usage.raw == usage
    with pytest.raises(ProviderError) as excinfo:
        _cost({"cost_rub": None, "cost": "1e999999999"})
    assert excinfo.value.error.details == {"reason": "invalid_cost"}


def test_decoder_rejects_huge_numeric_cost_before_domain_serialization() -> None:
    body = json.dumps(_payload({"cost_rub": "1e999999999"})).replace('"1e999999999"', "1e999999999")
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(decode_media_json(body))
    assert excinfo.value.error.details == {"reason": "invalid_cost"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_usage_raw_preserves_original_without_double_counting() -> None:
    """`usage.raw` хранит оба aliases, а `cost` строится один раз."""
    usage = {"output_units": 1, "cost_rub": "5.00", "cost": "5.00"}
    result = normalize_media_result(_payload(usage))
    assert result.usage == Usage(output_units=1.0, raw=usage)
    assert result.cost == Cost(amount=Decimal("5.00"), currency="RUB")


def test_usage_tokens_and_units_are_mapped() -> None:
    usage = {
        "input_tokens": 10,
        "output_tokens": 0,
        "total_tokens": 10,
        "input_units": 1,
        "output_units": 2,
        "duration_seconds": 5,
    }
    result = normalize_media_result(_payload(usage))
    assert result.usage is not None
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 0
    assert result.usage.total_tokens == 10
    assert result.usage.input_units == 1.0
    assert result.usage.output_units == 2.0
    assert result.usage.duration_seconds == 5.0
    assert result.usage.raw == usage


def test_absent_usage_fields_stay_unknown_not_zero() -> None:
    """Отсутствующий счётчик — `None`, а не `0`."""
    result = normalize_media_result(_payload({"output_units": 1}))
    assert result.usage is not None
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.total_tokens is None
    assert result.usage.input_units is None
    assert result.usage.duration_seconds is None


def test_zero_count_is_known_zero() -> None:
    result = normalize_media_result(_payload({"output_tokens": 0}))
    assert result.usage is not None
    assert result.usage.output_tokens == 0


@pytest.mark.parametrize(
    "value",
    [-1, 2.5, "abc", True],
    ids=["negative", "fractional", "string", "bool"],
)
def test_invalid_usage_counter_fails_closed(value: object) -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload({"output_units": value}))
    assert excinfo.value.error.details == {"reason": "invalid_usage"}


@pytest.mark.parametrize("field", ["input_units", "output_units", "duration_seconds"])
def test_huge_finite_decimal_usage_rejected_without_infinity(field: str) -> None:
    """Finite Decimal beyond float range is invalid, never an infinite Usage field."""
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload({field: Decimal("1e309")}))
    assert excinfo.value.error.details == {"reason": "invalid_usage"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


@pytest.mark.parametrize("failure", [OverflowError, ValueError])
def test_unit_conversion_failure_has_no_raw_exception_context(
    failure: type[Exception],
) -> None:
    canary = "secret-unit-conversion-canary"

    class FailingDecimal(Decimal):
        def __float__(self) -> float:
            raise failure(canary)

    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload({"output_units": FailingDecimal("2")}))
    assert excinfo.value.error.details == {"reason": "invalid_usage"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert canary not in "".join(traceback.format_exception(excinfo.value))


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens"])
def test_token_counter_accepts_local_max_and_rejects_oversize(field: str) -> None:
    maximum = 2**63 - 1  # local SQLite signed INTEGER, not a provider/model limit
    for value in (maximum, Decimal(maximum)):
        result = normalize_media_result(_payload({field: value}))
        assert result.usage is not None
        assert getattr(result.usage, field) == maximum
    for value in (maximum + 1, Decimal(maximum + 1), Decimal("1e4300"), 10**4300):
        with pytest.raises(ProviderError) as excinfo:
            normalize_media_result(_payload({field: value}))
        assert excinfo.value.error.details == {"reason": "invalid_usage"}
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None


def test_token_int_conversion_failure_has_no_raw_exception_context() -> None:
    canary = "secret-token-conversion-canary"

    class FailingDecimal(Decimal):
        def __int__(self) -> int:
            raise ValueError(canary)

    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload({"input_tokens": FailingDecimal("4")}))
    assert excinfo.value.error.details == {"reason": "invalid_usage"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert canary not in "".join(traceback.format_exception(excinfo.value))


def test_malformed_usage_object_fails_closed() -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload("not-an-object"))
    assert excinfo.value.error.details == {"reason": "invalid_usage"}


def test_decode_media_json_keeps_floats_as_decimal() -> None:
    """Санкционированный декодер не превращает деньги в двоичную дробь."""
    decoded = decode_media_json('{"cost_rub": 1.5, "output_units": 2}')
    assert isinstance(decoded, dict)
    assert decoded["cost_rub"] == Decimal("1.5")
    assert isinstance(decoded["cost_rub"], Decimal)
    assert decoded["output_units"] == 2


def test_decode_media_json_roundtrip_into_normalizer() -> None:
    body = json.dumps(_payload({"cost_rub": 5.0, "output_units": 1}))
    result = normalize_media_result(decode_media_json(body))
    assert result.cost == Cost(amount=Decimal("5.0"), currency="RUB")


def test_decode_media_json_rejects_malformed_body_safely() -> None:
    with pytest.raises(ProviderError) as excinfo:
        decode_media_json("{not json")
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "malformed_json"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_decode_media_json_rejects_nonfinite_even_in_unknown_usage(constant: str) -> None:
    body = json.dumps(_payload({"unknown_usage_key": 0})).replace(
        '"unknown_usage_key": 0', f'"unknown_usage_key": {constant}'
    )
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(decode_media_json(body))
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "malformed_json"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert constant not in "".join(traceback.format_exception(excinfo.value))


def test_decode_media_json_preserves_json_null() -> None:
    """Декодер не подменяет валидный JSON `null`; это отвергает нормализатор."""
    assert decode_media_json("null") is None
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(decode_media_json("null"))
    assert excinfo.value.error.details == {"reason": "response_not_object"}
