"""Денежные значения домена: `Cost` и `Usage`.

Инварианты (решения baseline E00, D11):

- `Cost` — это `amount + currency`; валюта обязательна, если сумма известна;
- ноль (`Decimal("0")`) — известная цена, отсутствие `Cost` — неизвестная цена;
  эти состояния не сводятся друг к другу;
- валюты не конвертируются и не складываются между собой: суммы группируются по
  валюте, а Decimal-арифметика выполняется в Python, а не SQL `SUM()`.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from aimedia.domain.base import CurrencyCode, DomainModel, ExactDecimal


class Cost(DomainModel):
    """Фактическая стоимость задания в конкретной валюте."""

    amount: ExactDecimal
    currency: CurrencyCode

    @property
    def is_zero(self) -> bool:
        """Известная нулевая стоимость — не то же самое, что неизвестная."""
        return self.amount == Decimal(0)


class Usage(DomainModel):
    """Данные об использованных ресурсах.

    Нормализованные поля не заменяют `raw`: provider может вернуть показатели,
    которых ещё нет в доменной модели, и терять их нельзя (инвариант 10).
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    input_units: float | None = None
    output_units: float | None = None

    duration_seconds: float | None = None
    characters: int | None = None

    raw: dict[str, object] = {}


def _add_exact_decimals(left: Decimal, right: Decimal) -> Decimal:
    """Сложить конечные суммы по цифрам, независимо от context и лимита int/str."""
    left_tuple, right_tuple = left.as_tuple(), right.as_tuple()
    exponent = min(int(left_tuple.exponent), int(right_tuple.exponent))

    def aligned_digits(digits: tuple[int, ...], value_exponent: int) -> list[int]:
        # Младший разряд первым; сдвиг экспоненты добавляет только нулевые разряды.
        aligned = [0] * (value_exponent - exponent) + list(reversed(digits))
        while len(aligned) > 1 and aligned[-1] == 0:
            aligned.pop()
        return aligned

    left_digits = aligned_digits(left_tuple.digits, int(left_tuple.exponent))
    right_digits = aligned_digits(right_tuple.digits, int(right_tuple.exponent))
    size = max(len(left_digits), len(right_digits))
    left_digits.extend([0] * (size - len(left_digits)))
    right_digits.extend([0] * (size - len(right_digits)))
    result: list[int] = []
    if left_tuple.sign == right_tuple.sign:
        carry = 0
        for a, b in zip(left_digits, right_digits, strict=True):
            carry, digit = divmod(a + b + carry, 10)
            result.append(digit)
        if carry:
            result.append(carry)
        sign = left_tuple.sign
    else:
        left_larger = list(reversed(left_digits)) >= list(reversed(right_digits))
        larger, smaller = (
            (left_digits, right_digits) if left_larger else (right_digits, left_digits)
        )
        sign = left_tuple.sign if left_larger else right_tuple.sign
        borrow = 0
        for a, b in zip(larger, smaller, strict=True):
            digit = a - b - borrow
            borrow = int(digit < 0)
            result.append(digit + 10 if borrow else digit)
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    if result == [0]:
        sign = 0
    return Decimal((sign, tuple(reversed(result)), exponent))


class CurrencyTotal(DomainModel):
    """Точная сумма по одной валюте."""

    currency: CurrencyCode
    amount: ExactDecimal
    job_count: int


class CostReport(DomainModel):
    """Снимок расходов: ненулевая цена, известный ноль и unknown раздельны.

    `known_priced_jobs` считает только задания с ненулевой известной стоимостью;
    `CurrencyTotal.job_count` включает и известные нулевые стоимости.
    """

    totals: tuple[CurrencyTotal, ...]
    total_jobs: int
    known_priced_jobs: int
    known_zero_jobs: int
    unknown_cost_jobs: int


def total_by_currency(costs: Iterable[Cost]) -> tuple[CurrencyTotal, ...]:
    """Сложить известные стоимости, не смешивая валюты.

    Неизвестная цена в суммировании не участвует: её нельзя выдать за ноль.
    Возвращается по одной записи на валюту, отсортированных по коду валюты, чтобы
    результат был детерминированным.
    """
    grouped: dict[str, tuple[Decimal, int]] = {}
    for cost in costs:
        amount, count = grouped.get(cost.currency, (Decimal(0), 0))
        grouped[cost.currency] = (_add_exact_decimals(amount, cost.amount), count + 1)
    return tuple(
        CurrencyTotal(currency=currency, amount=amount, job_count=count)
        for currency, (amount, count) in sorted(grouped.items())
    )
