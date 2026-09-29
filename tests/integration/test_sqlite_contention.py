"""Two owner-thread SQLite connections contend on the same temporary WAL database."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Event

from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    Job,
    JobKind,
    JobStatus,
    ModelRef,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
)
from aimedia.storage import DatabaseBusyError, DatabaseManager, PeeweeJobRepository
from aimedia.storage.models import JobRecord

PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="contention-test-model")
CREATED_AT = datetime(2026, 9, 28, tzinfo=UTC)


def _job(remote_id: str | None = None) -> Job:
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.CREATED,
        provider=PROVIDER,
        model=MODEL,
        request=ImageGenerationRequest(
            provider=PROVIDER,
            model=MODEL,
            prompt=CompiledPrompt(text="offline test", source_count=1),
        ),
        remote_ref=(
            RemoteJobRef(
                provider_id="polza",
                remote_job_id=remote_id,
                operation=RemoteOperation.MEDIA,
            )
            if remote_id is not None
            else None
        ),
        created_at=CREATED_AT,
    )


def _open(path: Path, timeout_ms: int) -> DatabaseManager:
    manager = DatabaseManager(path, busy_timeout_ms=timeout_ms)
    manager.open()
    return manager


def test_short_concurrent_repository_updates_commit_without_busy_snapshot(tmp_path: Path) -> None:
    """A second writer waits *before* reading its old row; both commits survive."""
    path = tmp_path / "database.sqlite3"
    first = _open(path, 3000)
    locked = Event()
    second_begin_attempted = Event()
    begin_sql: list[str] = []
    try:
        repository = PeeweeJobRepository(first)
        first_job = repository.save(_job())
        second_job = repository.save(_job())
        assert first_job.id is not None and second_job.id is not None
        first_update = first_job.model_copy(update={"remote_ref": _job("remote-first").remote_ref})
        second_update = second_job.model_copy(
            update={"remote_ref": _job("remote-second").remote_ref}
        )

        def trace_second_writer(sql: str) -> None:
            if sql.lstrip().upper().startswith("BEGIN"):
                begin_sql.append(sql)
                second_begin_attempted.set()

        # The callback belongs to this thread's connection, not the holder's.
        first.connection.set_trace_callback(trace_second_writer)

        class PausingRepository(PeeweeJobRepository):
            def _replace_children(self, record: JobRecord, job: Job) -> None:
                # save already updated the row and holds its write transaction.
                locked.set()
                if not second_begin_attempted.wait(5):
                    raise TimeoutError("second writer did not attempt BEGIN")
                super()._replace_children(record, job)

        def other_writer() -> Job:
            manager = _open(path, 3000)
            try:
                return PausingRepository(manager).save(first_update)
            finally:
                manager.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(other_writer)
            assert locked.wait(5)
            assert repository.save(second_update) == second_update
            assert future.result(timeout=5) == first_update
        assert begin_sql == ["BEGIN IMMEDIATE"]
        assert repository.get(first_job.id) == first_update
        assert repository.get(second_job.id) == second_update
        assert len(repository.list_recent()) == 2
    finally:
        first.connection.set_trace_callback(None)
        first.close()


def test_expired_busy_timeout_rolls_back_and_explicit_local_save_succeeds(tmp_path: Path) -> None:
    """A failed update leaves the original Job intact; caller retries only save."""
    path = tmp_path / "database.sqlite3"
    first = _open(path, 2000)
    attempt = Event()
    ready = Event()
    retry = Event()
    first_result: Queue[DatabaseBusyError] = Queue()
    try:
        repository = PeeweeJobRepository(first)
        original = repository.save(_job())
        assert original.id is not None
        updated = original.model_copy(update={"remote_ref": _job("known-remote-id").remote_ref})

        def other_writer() -> tuple[int | None, int | None]:
            manager = _open(path, 80)
            try:
                other = PeeweeJobRepository(manager)
                ready.set()
                if not attempt.wait(5):
                    raise TimeoutError("first attempt was not started")
                try:
                    other.save(updated)
                except DatabaseBusyError as exc:
                    first_result.put(exc)
                else:
                    raise AssertionError("write unexpectedly succeeded under held lock")
                if not retry.wait(5):
                    raise TimeoutError("explicit retry was not requested")
                saved = other.save(updated)
                again = other.save(saved)
                return saved.id, again.id
            finally:
                manager.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(other_writer)
            try:
                assert ready.wait(5)
                with first.database.atomic("IMMEDIATE"):
                    attempt.set()
                    error = first_result.get(timeout=5)
                    assert isinstance(error, DatabaseBusyError)
                    assert "busy_timeout" in str(error)
                    assert "known-remote-id" not in str(error)
                    assert repository.get(original.id) == original
                    assert len(repository.list_recent()) == 1
            finally:
                retry.set()
            saved_id, again_id = future.result(timeout=5)
        assert saved_id == again_id == original.id
        assert repository.get(original.id) == updated
        assert len(repository.list_recent()) == 1
    finally:
        retry.set()
        first.close()
