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


class CurrencyTotal(DomainModel):
    """Точная сумма по одной валюте."""

    currency: CurrencyCode
    amount: ExactDecimal
    job_count: int


def total_by_currency(costs: Iterable[Cost]) -> tuple[CurrencyTotal, ...]:
    """Сложить известные стоимости, не смешивая валюты.

    Неизвестная цена в суммировании не участвует: её нельзя выдать за ноль.
    Возвращается по одной записи на валюту, отсортированных по коду валюты, чтобы
    результат был детерминированным.
    """
    grouped: dict[str, list[Decimal]] = {}
    for cost in costs:
        grouped.setdefault(cost.currency, []).append(cost.amount)
    return tuple(
        CurrencyTotal(
            currency=currency,
            amount=sum(amounts, start=Decimal(0)),
            job_count=len(amounts),
        )
        for currency, amounts in sorted(grouped.items())
    )
