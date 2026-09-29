"""C07b: точный read-only отчёт по сохранённым Job без SQL SUM и FX."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from traceback import format_exception

import pytest

from aimedia.domain import (
    Artifact,
    ArtifactKind,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    CurrencyTotal,
    ImageGenerationRequest,
    Job,
    JobError,
    JobKind,
    JobStatus,
    ModelRef,
    ProviderRef,
    Usage,
)
from aimedia.storage import (
    InvalidStoredCostReportError,
    PeeweeCostReportRepository,
    PeeweeJobRepository,
    open_database,
)

PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="synthetic-cost-report-only")
PROMPT = CompiledPrompt(text="offline aggregation", source_count=1)
DAY = datetime(2026, 9, 28, tzinfo=UTC)


def _job(*, created_at: datetime = DAY, cost: Cost | None = None, failed: bool = False) -> Job:
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.FAILED if failed else JobStatus.CREATED,
        provider=PROVIDER,
        model=MODEL,
        request=ImageGenerationRequest(provider=PROVIDER, model=MODEL, prompt=PROMPT),
        compiled_prompt=PROMPT,
        cost=cost,
        usage=Usage(input_tokens=10, raw={"provider_cost": "999"}),
        artifacts=(
            (
                Artifact(
                    kind=ArtifactKind.IMAGE,
                    role=ArtifactRole.ORIGINAL,
                    local_path=Path("partial.webp"),
                ),
            )
            if failed
            else ()
        ),
        error=JobError(code="DOWNLOAD_FAILED", message="partial result") if failed else None,
        created_at=created_at,
        submitted_at=created_at if failed else None,
        completed_at=created_at if failed else None,
    )


def test_cost_report_exact_grouped_and_repeated_sync(tmp_path: Path) -> None:
    manager = open_database(tmp_path / "report.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        report = PeeweeCostReportRepository(manager)
        first = repository.save(_job(cost=Cost(amount="0.1", currency="RUB")))
        repository.save(_job(cost=Cost(amount="0.2", currency="RUB"), failed=True))
        repository.save(_job(cost=Cost(amount="0.0831", currency="USD")))
        repository.save(_job(cost=Cost(amount="0", currency="USD")))
        repository.save(_job())
        before = report.aggregate()
        assert before.totals == (
            CurrencyTotal(currency="RUB", amount=Decimal("0.3"), job_count=2),
            CurrencyTotal(currency="USD", amount=Decimal("0.0831"), job_count=2),
        )
        assert (
            before.total_jobs,
            before.known_priced_jobs,
            before.known_zero_jobs,
            before.unknown_cost_jobs,
        ) == (5, 3, 1, 1)
        # Re-saving an existing billing snapshot cannot create another charge.
        repository.save(first)
        assert report.aggregate() == before
        repository.save(first.model_copy(update={"cost": Cost(amount="0.2", currency="RUB")}))
        assert report.aggregate().totals[0].amount == Decimal("0.4")
        assert manager.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone() == (5,)
    finally:
        manager.close()


def test_cost_report_utc_half_open_boundaries_and_read_only(tmp_path: Path) -> None:
    manager = open_database(tmp_path / "report.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        report = PeeweeCostReportRepository(manager)
        for moment in (
            DAY - timedelta(microseconds=1),
            DAY,
            DAY + timedelta(microseconds=1),
            DAY + timedelta(days=1),
        ):
            repository.save(_job(created_at=moment, cost=Cost(amount="0.1", currency="RUB")))
        rows_before = manager.database.execute_sql('SELECT * FROM "jobs" ORDER BY "id"').fetchall()
        result = report.aggregate(start=DAY, end=DAY + timedelta(days=1))
        assert result.total_jobs == 2
        assert result.totals == (CurrencyTotal(currency="RUB", amount=Decimal("0.2"), job_count=2),)
        # Exact second excludes the fractional timestamp immediately after it.
        assert report.aggregate(start=DAY, end=DAY + timedelta(microseconds=1)).total_jobs == 1
        assert report.aggregate(start=DAY, end=DAY).total_jobs == 0
        offset_start = DAY.astimezone(timezone(timedelta(hours=3)))
        assert report.aggregate(start=offset_start, end=DAY + timedelta(days=1)).total_jobs == 2
        assert report.aggregate(start=DAY + timedelta(hours=3), end=None).total_jobs == 1
        assert (
            manager.database.execute_sql('SELECT * FROM "jobs" ORDER BY "id"').fetchall()
            == rows_before
        )
        with pytest.raises(ValueError, match="timezone-aware"):
            report.aggregate(start=DAY.replace(tzinfo=None))
        with pytest.raises(ValueError, match="timezone-aware"):
            report.aggregate(end=DAY.replace(tzinfo=None))
        with pytest.raises(ValueError, match="позже"):
            report.aggregate(start=DAY + timedelta(days=1), end=DAY)
    finally:
        manager.close()


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("created_at", "secret-prompt-path-canary-invalid-date"),
        ("created_at", "2026-09-28T00:00:00 secret-prompt-path-canary"),
        ("cost_amount", "secret-prompt-path-canary-invalid-amount"),
        ("cost_currency", "secret-prompt-path-canary-invalid-currency"),
    ],
)
def test_cost_report_corrupt_snapshot_is_safe_and_read_only(
    tmp_path: Path, column: str, corrupt_value: str
) -> None:
    manager = open_database(tmp_path / "report.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        repository.save(_job(cost=Cost(amount="1.23", currency="RUB")))
        manager.database.execute_sql(
            f'UPDATE "jobs" SET "{column}" = ? WHERE "id" = 1', (corrupt_value,)
        )
        before = manager.database.execute_sql('SELECT * FROM "jobs"').fetchall()
        report = PeeweeCostReportRepository(manager)
        with pytest.raises(InvalidStoredCostReportError) as caught:
            report.aggregate()
        assert corrupt_value not in "".join(format_exception(caught.value))
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert manager.database.execute_sql('SELECT * FROM "jobs"').fetchall() == before
    finally:
        manager.close()


def test_cost_report_persists_and_sums_over_4300_digits(tmp_path: Path) -> None:
    manager = open_database(tmp_path / "report.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        report = PeeweeCostReportRepository(manager)
        huge = "9" * 4400
        repository.save(_job(cost=Cost(amount=f"{huge}.125", currency="USD")))
        repository.save(_job(cost=Cost(amount="0.875", currency="USD")))
        stored = manager.database.execute_sql(
            'SELECT "cost_amount" FROM "jobs" ORDER BY "cost_amount" DESC'
        ).fetchall()
        assert (f"{huge}.125",) in stored
        with localcontext() as context:
            context.prec = 6
            actual = report.aggregate().totals
        assert actual == (
            CurrencyTotal(currency="USD", amount=Decimal(f"1{'0' * 4400}.000"), job_count=2),
        )
    finally:
        manager.close()


def test_cost_report_accumulates_exactly_under_low_decimal_precision(tmp_path: Path) -> None:
    manager = open_database(tmp_path / "report.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        report = PeeweeCostReportRepository(manager)
        for amount in ("123456789012345678901234567890.1234", "0.0831", "-0.1", "0"):
            repository.save(_job(cost=Cost(amount=amount, currency="USD")))
        with localcontext() as context:
            context.prec = 6
            actual = report.aggregate().totals
        assert actual == (
            CurrencyTotal(
                currency="USD", amount=Decimal("123456789012345678901234567890.1065"), job_count=4
            ),
        )
    finally:
        manager.close()
