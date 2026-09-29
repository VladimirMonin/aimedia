"""Read-only Decimal-отчёт по снимкам стоимости Job в SQLite."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from peewee import SqliteDatabase
from pydantic import ValidationError

from aimedia.domain.costs import Cost, CostReport, CurrencyTotal, _add_exact_decimals
from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import InvalidStoredCostReportError
from aimedia.storage.models import JobRecord


def _utc_boundary(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("граница отчёта должна быть timezone-aware")
    return value.astimezone(UTC)


class PeeweeCostReportRepository:
    """Суммировать только сохранённые billing snapshots, по одной строке на Job.

    Менеджер проверяет владельца и открытое состояние при каждом вызове; метод
    не открывает соединений и не пишет ни в SQLite, ни в историю событий.
    Фильтрация выполняется по разобранным datetime: текстовые ISO timestamps
    с различной точностью нельзя
    безопасно сравнивать лексикографически в SQL.
    """

    def __init__(self, manager: DatabaseManager) -> None:
        if not isinstance(manager, DatabaseManager):
            raise TypeError("PeeweeCostReportRepository требует DatabaseManager")
        self._manager = manager

    @property
    def _database(self) -> SqliteDatabase:
        return self._manager.database

    def aggregate(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> CostReport:
        """Вернуть суммы по валютам за полуоткрытый UTC-интервал создания Job."""
        database = self._database
        lower = _utc_boundary(start)
        upper = _utc_boundary(end)
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("начало интервала отчёта позже конца")

        grouped: dict[str, tuple[Decimal, int]] = {}
        total_jobs = known_priced_jobs = known_zero_jobs = unknown_cost_jobs = 0
        with database.bind_ctx([JobRecord]):
            rows = JobRecord.select(
                JobRecord.created_at, JobRecord.cost_amount, JobRecord.cost_currency
            ).tuples()
            for created_at, amount_text, currency in rows:
                created: datetime | None
                try:
                    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except (ValueError, TypeError, AttributeError):
                    created = None
                if created is None or created.tzinfo is None or created.utcoffset() is None:
                    raise InvalidStoredCostReportError() from None
                created = created.astimezone(UTC)
                if (lower is not None and created < lower) or (
                    upper is not None and created >= upper
                ):
                    continue
                total_jobs += 1
                if amount_text is None:
                    unknown_cost_jobs += 1
                    continue
                # Cost validates finite Decimal and currency; raw usage is not a charge.
                cost: Cost | None
                try:
                    cost = Cost(amount=amount_text, currency=currency)
                except (ValidationError, ValueError, TypeError):
                    cost = None
                if cost is None:
                    raise InvalidStoredCostReportError() from None
                if cost.is_zero:
                    known_zero_jobs += 1
                else:
                    known_priced_jobs += 1
                amount, count = grouped.get(cost.currency, (Decimal(0), 0))
                grouped[cost.currency] = (_add_exact_decimals(amount, cost.amount), count + 1)

        return CostReport(
            totals=tuple(
                CurrencyTotal(currency=currency, amount=amount, job_count=count)
                for currency, (amount, count) in sorted(grouped.items())
            ),
            total_jobs=total_jobs,
            known_priced_jobs=known_priced_jobs,
            known_zero_jobs=known_zero_jobs,
            unknown_cost_jobs=unknown_cost_jobs,
        )
