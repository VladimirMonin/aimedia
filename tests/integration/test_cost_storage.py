"""C07a: точный billing snapshot в SQLite и безопасные post-commit события."""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from aimedia.domain import (
    Artifact,
    ArtifactKind,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    ImageGenerationRequest,
    Job,
    JobError,
    JobKind,
    JobStatus,
    ModelRef,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    Usage,
)
from aimedia.logging import EventLogger, correlation
from aimedia.storage import (
    NestedStorageTransactionError,
    PeeweeJobRepository,
    log_cost_recorded,
    log_usage_recorded,
    open_database,
)

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="synthetic-cost-history-only")
CANARY = "private-prompt-path-correlation-raw-token"
PROMPT = CompiledPrompt(text=CANARY, source_count=1)


def _job(*, cost: Cost | None = None, usage: Usage | None = None, failed: bool = False) -> Job:
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.FAILED if failed else JobStatus.CREATED,
        provider=PROVIDER,
        model=MODEL,
        request=ImageGenerationRequest(provider=PROVIDER, model=MODEL, prompt=PROMPT),
        compiled_prompt=PROMPT,
        cost=cost,
        usage=usage,
        remote_ref=(
            RemoteJobRef(provider_id="polza", remote_job_id=CANARY, operation=RemoteOperation.MEDIA)
            if failed
            else None
        ),
        artifacts=(
            (
                Artifact(
                    kind=ArtifactKind.IMAGE, role=ArtifactRole.ORIGINAL, local_path=Path(CANARY)
                ),
            )
            if failed
            else ()
        ),
        error=JobError(code="ARTIFACT_DOWNLOAD_FAILED", message="download failed")
        if failed
        else None,
        created_at=NOW,
        submitted_at=NOW if failed else None,
        completed_at=NOW if failed else None,
    )


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.parametrize("amount", ["0.0831", "0.1", "0.2", "0"])
def test_decimal_text_roundtrip_without_float(tmp_path: Path, amount: str) -> None:
    path = tmp_path / "billing.sqlite3"
    first = open_database(path)
    try:
        saved = PeeweeJobRepository(first).save(_job(cost=Cost(amount=amount, currency="rub")))
        assert saved.id is not None
        job_id = saved.id
        raw = first.database.execute_sql(
            'SELECT "cost_amount", "cost_currency", typeof("cost_amount") '
            'FROM "jobs" WHERE "id" = ?',
            (job_id,),
        ).fetchone()
    finally:
        first.close()
    second = open_database(path)
    try:
        restored = PeeweeJobRepository(second).get(job_id)
    finally:
        second.close()
    assert raw == (amount, "RUB", "text")
    assert restored is not None and restored.cost is not None
    assert restored.cost.amount == Decimal(amount)
    assert format(restored.cost.amount, "f") == amount


def test_unknown_zero_and_currencies_remain_separate_snapshots(tmp_path: Path) -> None:
    manager = open_database(tmp_path / "billing.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        ids = [
            repository.save(_job(cost=cost)).id
            for cost in (None, Cost(amount="0", currency="RUB"), Cost(amount="0.1", currency="USD"))
        ]
        assert all(job_id is not None for job_id in ids)
        rows = manager.database.execute_sql(
            'SELECT "cost_amount", "cost_currency" FROM "jobs" ORDER BY "id"'
        ).fetchall()
        restored = [repository.get(job_id) for job_id in ids if job_id is not None]
    finally:
        manager.close()
    assert rows == [(None, None), ("0", "RUB"), ("0.1", "USD")]
    assert restored[0] is not None and restored[0].cost is None
    assert restored[1] is not None and restored[1].cost == Cost(amount="0", currency="RUB")
    assert restored[2] is not None and restored[2].cost == Cost(amount="0.1", currency="USD")


def test_repeated_billing_sync_updates_one_job_without_duplicate_events(tmp_path: Path) -> None:
    logger_stream = io.StringIO()
    logger = EventLogger(stream=logger_stream, min_level="DEBUG")
    manager = open_database(tmp_path / "billing.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        created = repository.save(_job(), logger=logger)
        assert created.id is not None
        usage = Usage(input_tokens=2, total_tokens=3, raw={"provider_private": CANARY})
        billing = created.model_copy(
            update={"cost": Cost(amount="0.0831", currency="RUB"), "usage": usage}
        )
        first = repository.save(billing, logger=logger)
        second = repository.save(billing, logger=logger)
        third = repository.save(first, logger=logger)
        assert [first.id, second.id, third.id] == [created.id] * 3
        assert manager.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone() == (1,)
        raw = manager.database.execute_sql(
            'SELECT "usage_json", "cost_amount" FROM "jobs" WHERE "id" = ?', (created.id,)
        ).fetchone()
        assert raw is not None
        assert json.loads(raw[0])["raw"] == {"provider_private": CANARY}
        assert raw[1] == "0.0831"
        assert third.usage is not None and third.usage.raw == usage.raw
    finally:
        manager.close()
    assert [record["event"] for record in _records(logger_stream)] == [
        "job_created",
        "usage_recorded",
        "cost_recorded",
    ]
    assert _records(logger_stream)[1]["details"] == {
        "input_count": 2,
        "total_count": 3,
        "raw_field_count": 1,
    }
    assert _records(logger_stream)[2]["details"] == {"amount": "0.0831", "currency": "RUB"}
    assert CANARY not in logger_stream.getvalue()


def test_two_owners_log_each_committed_billing_snapshot(tmp_path: Path) -> None:
    """A later commit before the first event must not replace the first save's snapshot."""
    path = tmp_path / "billing.sqlite3"
    stream = io.StringIO()
    logger = EventLogger(stream=stream, min_level="DEBUG")
    first_manager = open_database(path)
    second_may_write = Event()
    second_committed = Event()
    first_logged = Event()
    try:
        original = PeeweeJobRepository(first_manager).save(_job())
        assert original.id is not None
        first_update = original.model_copy(
            update={"cost": Cost(amount="0.1", currency="RUB"), "usage": Usage(input_tokens=1)}
        )
        second_update = original.model_copy(
            update={"cost": Cost(amount="0.2", currency="RUB"), "usage": Usage(input_tokens=2)}
        )

        class FirstRepository(PeeweeJobRepository):
            def get(self, job_id: int) -> Job | None:
                # Old save read *after* commit; hold that read until the second
                # owner commits. New save reads inside its own transaction.
                if self._database.transaction_depth() == 0:
                    second_may_write.set()
                    if not second_committed.wait(5):
                        raise TimeoutError("second owner did not commit before post-commit get")
                return super().get(job_id)

            def _log_save(self, logger: EventLogger | None, *, previous: Any, saved: Job) -> None:
                # New save reaches this hook only after its final commit; do not
                # hold a SQLite write transaction while waiting for the other owner.
                assert self._database.transaction_depth() == 0
                second_may_write.set()
                try:
                    if not second_committed.wait(5):
                        raise TimeoutError("second owner did not commit before first event")
                    super()._log_save(logger, previous=previous, saved=saved)
                finally:
                    first_logged.set()

        class SecondRepository(PeeweeJobRepository):
            def _log_save(self, logger: EventLogger | None, *, previous: Any, saved: Job) -> None:
                # Called after the second commit; keep event ordering deterministic.
                assert self._database.transaction_depth() == 0
                second_committed.set()
                if not first_logged.wait(5):
                    raise TimeoutError("first owner did not finish logging")
                super()._log_save(logger, previous=previous, saved=saved)

        def write_second() -> Job:
            manager = open_database(path)
            try:
                if not second_may_write.wait(5):
                    raise TimeoutError("first owner did not commit")
                return SecondRepository(manager).save(second_update, logger=logger)
            finally:
                manager.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write_second)
            try:
                first_saved = FirstRepository(first_manager).save(first_update, logger=logger)
            finally:
                second_may_write.set()
                first_logged.set()
            second_saved = future.result(timeout=5)
        final = PeeweeJobRepository(first_manager).get(original.id)
    finally:
        second_may_write.set()
        first_logged.set()
        first_manager.close()

    assert first_saved == first_update
    assert second_saved == second_update
    assert final is not None and final.cost == Cost(amount="0.2", currency="RUB")
    billing_events = [
        (record["event"], record["details"])
        for record in _records(stream)
        if record["event"] in {"usage_recorded", "cost_recorded"}
    ]
    assert billing_events == [
        ("usage_recorded", {"input_count": 1, "raw_field_count": 0}),
        ("cost_recorded", {"amount": "0.1", "currency": "RUB"}),
        ("usage_recorded", {"input_count": 2, "raw_field_count": 0}),
        ("cost_recorded", {"amount": "0.2", "currency": "RUB"}),
    ]


def test_failed_after_remote_success_keeps_known_billing_and_partial_artifact(
    tmp_path: Path,
) -> None:
    manager = open_database(tmp_path / "billing.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        usage = Usage(output_units=1.5, raw={"provider_payload": {"opaque": CANARY}})
        saved = repository.save(
            _job(cost=Cost(amount="0.2", currency="USD"), usage=usage, failed=True)
        )
        assert saved.id is not None
        restored = repository.get(saved.id)
        row = manager.database.execute_sql(
            'SELECT "status", "cost_amount", "cost_currency", "usage_json" '
            'FROM "jobs" WHERE "id" = ?',
            (saved.id,),
        ).fetchone()
    finally:
        manager.close()
    assert restored is not None and restored.status is JobStatus.FAILED
    assert restored.result is None and restored.error is not None
    assert (
        restored.remote_ref is not None and restored.remote_ref.operation is RemoteOperation.MEDIA
    )
    assert len(restored.artifacts) == 1 and restored.artifacts[0].role is ArtifactRole.ORIGINAL
    assert restored.cost == Cost(amount="0.2", currency="USD")
    assert restored.usage == usage
    assert row is not None and row[:3] == ("failed", "0.2", "USD")
    assert json.loads(row[3])["raw"] == usage.raw


def test_events_only_after_commit_and_without_raw_or_correlation(tmp_path: Path) -> None:
    stream = io.StringIO()
    logger = EventLogger(stream=stream, min_level="DEBUG")
    child = logger.child(command=CANARY, provider=CANARY, remote_job_id=CANARY, job_id=CANARY)
    manager = open_database(tmp_path / "billing.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        with correlation(command=CANARY, provider=CANARY, remote_job_id=CANARY):
            with manager.database.atomic():
                with pytest.raises(NestedStorageTransactionError):
                    repository.save(
                        _job(
                            cost=Cost(amount="0", currency="RUB"), usage=Usage(raw={CANARY: CANARY})
                        ),
                        logger=child,
                    )
                assert _records(stream) == []
            assert manager.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone() == (0,)
            saved = repository.save(
                _job(cost=Cost(amount="0", currency="RUB"), usage=Usage(raw={CANARY: CANARY})),
                logger=child,
            )
            assert saved.id is not None
            assert repository.get(saved.id) is not None
            repository.save(saved, logger=child)
    finally:
        manager.close()
    records = _records(stream)
    assert [record["event"] for record in records] == [
        "job_created",
        "usage_recorded",
        "cost_recorded",
    ]
    assert records[1]["details"] == {"raw_field_count": 1}
    assert records[2]["details"] == {"amount": "0", "currency": "RUB"}
    assert all(record["job_id"] == str(saved.id) for record in records)
    assert all("command" not in record and "provider" not in record for record in records)
    assert all("remote_job_id" not in record for record in records)
    assert CANARY not in stream.getvalue()
    assert str(tmp_path) not in stream.getvalue()


def test_old_tainted_billing_columns_do_not_flow_into_change_events(tmp_path: Path) -> None:
    stream = io.StringIO()
    logger = EventLogger(stream=stream, min_level="DEBUG")
    manager = open_database(tmp_path / "billing.sqlite3")
    try:
        repository = PeeweeJobRepository(manager)
        created = repository.save(_job())
        assert created.id is not None
        manager.database.execute_sql(
            'UPDATE "jobs" SET "cost_amount" = ?, "cost_currency" = ?, '
            '"usage_json" = ? WHERE "id" = ?',
            (CANARY, CANARY, CANARY, created.id),
        )
        repository.save(
            created.model_copy(
                update={
                    "cost": Cost(amount="0.1", currency="USD"),
                    "usage": Usage(raw={CANARY: CANARY}),
                }
            ),
            logger=logger,
        )
    finally:
        manager.close()
    assert [record["event"] for record in _records(stream)] == ["usage_recorded", "cost_recorded"]
    assert CANARY not in stream.getvalue()


def test_direct_event_helpers_reject_unvalidated_values_without_leaking_them() -> None:
    stream = io.StringIO()
    logger = EventLogger(stream=stream, min_level="DEBUG")
    for amount, currency in ((CANARY, "RUB"), ("0.1", CANARY), (float("nan"), "RUB")):
        with pytest.raises(ValueError) as caught:
            log_cost_recorded(logger, job_id=1, amount=amount, currency=currency)  # type: ignore[arg-type]
        assert CANARY not in str(caught.value)
    tainted_usage = Usage(raw={CANARY: CANARY}).model_copy(update={"input_tokens": CANARY})
    with pytest.raises(ValueError) as caught:
        log_usage_recorded(logger, job_id=1, usage=tainted_usage)
    assert CANARY not in str(caught.value)
    assert stream.getvalue() == ""
    assert log_cost_recorded(None, job_id=1, amount="0.1", currency="USD") is None
    assert log_usage_recorded(None, job_id=1, usage=Usage()) is None
