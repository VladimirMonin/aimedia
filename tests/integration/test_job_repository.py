"""Round trip Job через SQLite: снапшоты, порядок и изоляция записей (E04, C06b).

Проверяется реализованный контракт `JobRepository` (`src/aimedia/domain/ports.py`)
на реальном SQLite в `tmp_path`: сохранённый агрегат переживает закрытие и
повторное открытие базы, порядок prompt sources/inputs/artifacts и remote operation
восстанавливаются точно, failed Job сохраняет partial metadata без ложного
`completed`, а два Job не смешивают свои дочерние строки.

Загрузка Job не обращается к Model Registry: в тестах участвует логическая модель,
которой в Registry заведомо нет, и история всё равно восстанавливается — значит,
читаются только сохранённые snapshot-поля.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest

from aimedia.domain import (
    Artifact,
    ArtifactKind,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    ImageGenerationRequest,
    InputKind,
    InputRef,
    Job,
    JobError,
    JobKind,
    JobResult,
    JobStatus,
    ModelRef,
    PromptSource,
    PromptSourceKind,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    Usage,
)
from aimedia.storage import (
    DatabaseClosedError,
    DatabaseOwnershipError,
    PeeweeCostReportRepository,
    PeeweeJobRepository,
    open_database,
)

DATABASE_FILENAME = "database.sqlite3"
CREATED_AT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 9, 28, 8, 0, 21, tzinfo=UTC)

PROVIDER = ProviderRef(id="polza")
# Модели с таким id нет в Registry: история обязана читаться без его участия.
MODEL = ModelRef(id="synthetic-history-only-model")
REF_SHA256 = "a" * 64


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILENAME


def _request(**overrides: object) -> ImageGenerationRequest:
    payload: dict[str, object] = {
        "provider": PROVIDER,
        "model": MODEL,
        "prompt": CompiledPrompt(
            text="base text\n\ncamera frontal\n\nstyle text",
            source_count=3,
            sha256="b" * 64,
        ),
        "aspect_ratio": "16:9",
        "resolution": "2K",
        "quality": "high",
        "seed": 481,
        "max_images": 2,
    }
    payload.update(overrides)
    return ImageGenerationRequest(**payload)  # type: ignore[arg-type]


def _prompt_sources() -> list[PromptSource]:
    return [
        PromptSource(
            kind=PromptSourceKind.FILE,
            text="base text",
            position=0,
            path=Path("prompts/base.md"),
        ),
        PromptSource(kind=PromptSourceKind.INLINE, text="camera frontal", position=1),
        PromptSource(
            kind=PromptSourceKind.FILE,
            text="style text",
            position=2,
            path=Path("prompts/style.md"),
        ),
    ]


def _inputs() -> list[InputRef]:
    return [
        InputRef(
            kind=InputKind.IMAGE,
            path=Path("refs/robot.png"),
            position=0,
            mime_type="image/png",
            size_bytes=4096,
            sha256=REF_SHA256,
            metadata={"width": 2048, "height": 2048},
        ),
        InputRef(
            kind=InputKind.IMAGE,
            path=Path("refs/lab.png"),
            position=1,
            mime_type="image/png",
            size_bytes=5120,
            sha256="c" * 64,
        ),
    ]


def _original_artifact() -> Artifact:
    return Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.ORIGINAL,
        local_path=Path("out/481/provider_original.webp"),
        remote_url="https://cdn.example/481/original.webp",
        mime_type="image/webp",
        size_bytes=1024,
        sha256="d" * 64,
        metadata={"format": "WEBP"},
    )


def _final_artifact() -> Artifact:
    return Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.FINAL,
        local_path=Path("out/481/result_001.webp"),
        mime_type="image/webp",
        size_bytes=1854921,
        metadata={"width": 2048, "height": 2048},
    )


def _completed_job() -> Job:
    """Completed image Job со всеми заполненными snapshot-полями."""
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.COMPLETED,
        provider=PROVIDER,
        model=MODEL,
        remote_model_id="seedream-5-pro-2026-09",
        request=_request(),
        inputs=_inputs(),
        prompt_sources=_prompt_sources(),
        compiled_prompt=CompiledPrompt(
            text="base text\n\ncamera frontal\n\nstyle text",
            source_count=3,
            sha256="b" * 64,
        ),
        result=JobResult(
            artifacts=[_final_artifact()],
            content="done",
            metadata={"trace_id": "trc_1"},
        ),
        artifacts=[_original_artifact()],
        usage=Usage(output_units=2.0, duration_seconds=21.5, raw={"cost_rub": 4.0}),
        cost=Cost(amount="0.0831", currency="rub"),
        remote_ref=RemoteJobRef(
            provider_id="polza",
            remote_job_id="aig_abc123",
            operation=RemoteOperation.MEDIA,
        ),
        created_at=CREATED_AT,
        submitted_at=CREATED_AT,
        started_at=CREATED_AT,
        completed_at=COMPLETED_AT,
    )


def _failed_job() -> Job:
    """Failed Job с partial metadata: error, usage, cost и частичный artifact."""
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.FAILED,
        provider=PROVIDER,
        model=MODEL,
        request=_request(),
        prompt_sources=_prompt_sources(),
        compiled_prompt=CompiledPrompt(text="base text", source_count=1),
        artifacts=[_original_artifact()],
        usage=Usage(output_units=1.0),
        cost=Cost(amount="4.00", currency="RUB"),
        remote_ref=RemoteJobRef(
            provider_id="polza",
            remote_job_id="aig_failed1",
            operation=RemoteOperation.MEDIA,
        ),
        error=JobError(
            code="ARTIFACT_DOWNLOAD_FAILED",
            message="download failed",
            provider_code="BAD_GATEWAY",
            provider_message="upstream 502",
            retryable=True,
            details={"trace_id": "trc_502"},
        ),
        created_at=CREATED_AT,
        submitted_at=CREATED_AT,
        completed_at=COMPLETED_AT,
    )


def test_completed_job_survives_close_and_reopen(tmp_path: Path) -> None:
    """Полный агрегат восстанавливается после закрытия и повторного открытия БД."""
    path = _database_path(tmp_path)
    job = _completed_job()

    first = open_database(path)
    try:
        saved = PeeweeJobRepository(first).save(job)
        assert saved.id is not None
        job_id = saved.id
    finally:
        first.close()

    second = open_database(path)
    try:
        restored = PeeweeJobRepository(second).get(job_id)
    finally:
        second.close()

    assert restored == job.model_copy(update={"id": job_id})
    assert restored is not None
    assert restored.prompt_sources == job.prompt_sources
    assert restored.inputs == job.inputs
    assert restored.compiled_prompt is not None
    assert restored.compiled_prompt.text == "base text\n\ncamera frontal\n\nstyle text"
    assert restored.compiled_prompt.source_count == 3
    assert restored.cost is not None
    assert restored.cost.amount == Decimal("0.0831")
    assert restored.cost.currency == "RUB"
    assert restored.created_at.tzinfo is not None
    assert restored.completed_at == COMPLETED_AT
    assert restored.usage is not None
    assert restored.usage.raw == {"cost_rub": 4.0}


def test_artifact_collections_and_prompt_order_are_preserved(tmp_path: Path) -> None:
    """Порядок источников и принадлежность артефактов result сохраняются точно."""
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_completed_job()).id
        assert job_id is not None
        restored = repository.get(job_id)
    finally:
        manager.close()

    assert restored is not None
    assert [source.position for source in restored.prompt_sources] == [0, 1, 2]
    assert [source.kind for source in restored.prompt_sources] == [
        PromptSourceKind.FILE,
        PromptSourceKind.INLINE,
        PromptSourceKind.FILE,
    ]
    assert restored.prompt_sources[0].path == Path("prompts/base.md")
    assert restored.prompt_sources[1].path is None
    assert [ref.position for ref in restored.inputs] == [0, 1]
    assert restored.inputs[0].sha256 == REF_SHA256
    assert restored.inputs[0].metadata == {"width": 2048, "height": 2048}

    # FINAL принадлежит result, ORIGINAL — агрегатному списку: различие не теряется.
    assert restored.artifacts == (_original_artifact(),)
    assert restored.result is not None
    assert restored.result.artifacts == (_final_artifact(),)
    assert restored.result.content == "done"
    assert restored.result.metadata == {"trace_id": "trc_1"}


def test_remote_operation_is_restored_with_remote_job_id(tmp_path: Path) -> None:
    """Remote ID и тип операции восстанавливаются вместе, без угадывания endpoint."""
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_completed_job()).id
        assert job_id is not None
        restored = repository.get(job_id)
        row = manager.database.execute_sql(
            'SELECT "remote_job_id", "operation" FROM "jobs" WHERE "id" = ?', (job_id,)
        ).fetchone()
    finally:
        manager.close()

    assert row == ("aig_abc123", "media")
    assert restored is not None
    assert restored.remote_ref is not None
    assert restored.remote_ref.remote_job_id == "aig_abc123"
    assert restored.remote_ref.operation is RemoteOperation.MEDIA
    assert restored.remote_model_id == "seedream-5-pro-2026-09"


def test_failed_job_keeps_partial_metadata(tmp_path: Path) -> None:
    """Failed Job сохраняет error, usage, cost и partial artifact, оставаясь failed."""
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_failed_job()).id
        assert job_id is not None
        restored = repository.get(job_id)
        status_row = manager.database.execute_sql(
            'SELECT "status", "cost_amount", "cost_currency", "error_code" FROM "jobs" '
            'WHERE "id" = ?',
            (job_id,),
        ).fetchone()
    finally:
        manager.close()

    assert status_row == ("failed", "4.00", "RUB", "ARTIFACT_DOWNLOAD_FAILED")
    assert restored is not None
    assert restored.status is JobStatus.FAILED
    assert restored.result is None
    assert restored.error is not None
    assert restored.error.code == "ARTIFACT_DOWNLOAD_FAILED"
    assert restored.error.provider_code == "BAD_GATEWAY"
    assert restored.error.provider_message == "upstream 502"
    assert restored.error.retryable is True
    assert restored.error.details == {"trace_id": "trc_502"}
    assert restored.cost is not None
    assert restored.cost.amount == Decimal("4.00")
    assert restored.usage is not None
    assert restored.usage.output_units == 1.0
    assert restored.artifacts == (_original_artifact(),)


def test_two_jobs_do_not_share_children_and_recent_order_is_newest_first(
    tmp_path: Path,
) -> None:
    """Дочерние строки Job изолированы, а список последних идёт от новых к старым."""
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        first_id = repository.save(_completed_job()).id
        second_id = repository.save(_failed_job()).id
        assert first_id is not None
        assert second_id is not None
        assert first_id != second_id

        completed = repository.get(first_id)
        failed = repository.get(second_id)
        recent = repository.list_recent()
        failed_only = repository.list_recent(statuses=[JobStatus.FAILED])
        child_counts = {
            table: manager.database.execute_sql(
                f'SELECT "job_id", COUNT(*) FROM "{table}" GROUP BY "job_id" ORDER BY "job_id"'
            ).fetchall()
            for table in ("prompt_sources", "inputs", "artifacts")
        }
    finally:
        manager.close()

    assert completed is not None
    assert failed is not None
    assert completed.status is JobStatus.COMPLETED
    assert failed.status is JobStatus.FAILED
    assert len(completed.prompt_sources) == 3
    assert len(failed.prompt_sources) == 3
    assert completed.prompt_sources[2].text == "style text"
    assert failed.prompt_sources[2].text == "style text"
    assert len(completed.inputs) == 2
    assert failed.inputs == []
    assert [job.id for job in recent] == [second_id, first_id]
    assert [job.id for job in failed_only] == [second_id]
    assert child_counts["prompt_sources"] == [(first_id, 3), (second_id, 3)]
    assert child_counts["inputs"] == [(first_id, 2)]
    assert child_counts["artifacts"] == [(first_id, 2), (second_id, 1)]


def test_resaving_job_updates_in_place(tmp_path: Path) -> None:
    """Повторный save того же Job обновляет строку, а не добавляет вторую."""
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_completed_job()).id
        assert job_id is not None

        saved = repository.get(job_id)
        assert saved is not None
        resaved_id = repository.save(_failed_job().model_copy(update={"id": job_id})).id
        restored = repository.get(job_id)
        job_count = manager.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone()
    finally:
        manager.close()

    assert resaved_id == job_id
    assert job_count == (1,)
    assert restored is not None
    assert restored.status is JobStatus.FAILED
    assert restored.error is not None
    assert restored.error.code == "ARTIFACT_DOWNLOAD_FAILED"
    assert restored.result is None


def test_get_missing_job_returns_none(tmp_path: Path) -> None:
    """Несуществующий ID — `None`, а не исключение Peewee."""
    manager = open_database(_database_path(tmp_path))
    try:
        assert PeeweeJobRepository(manager).get(999) is None
    finally:
        manager.close()


def test_repositories_refuse_foreign_thread_and_closed_manager(tmp_path: Path) -> None:
    """Каждый публичный вызов проверяет manager до Peewee autoconnect и записи."""
    path = _database_path(tmp_path)
    manager = open_database(path)
    repository = PeeweeJobRepository(manager)
    report = PeeweeCostReportRepository(manager)
    job = _failed_job()
    try:
        saved = repository.save(job)
        assert saved.id is not None
        operations = (
            lambda: repository.save(job),
            lambda: repository.get(saved.id),
            repository.list_recent,
            report.aggregate,
            lambda: manager.database,
            lambda: manager.connection,
        )
        with patch.object(manager._database, "connect", wraps=manager._database.connect) as connect:
            errors: list[Exception] = []

            def use_from_other_thread() -> None:
                for operation in operations:
                    try:
                        operation()
                    except Exception as exc:
                        errors.append(exc)

            thread = Thread(target=use_from_other_thread)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert len(errors) == len(operations)
            assert all(isinstance(exc, DatabaseOwnershipError) for exc in errors)
            connect.assert_not_called()

        assert manager.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone() == (1,)
        manager.close()
        with patch.object(manager._database, "connect", wraps=manager._database.connect) as connect:
            for operation in operations:
                with pytest.raises(DatabaseClosedError):
                    operation()
            connect.assert_not_called()
        assert not manager.is_open
    finally:
        manager.close()

    with open_database(path) as reopened:
        assert reopened.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone() == (1,)


def test_repositories_require_manager_not_raw_database(tmp_path: Path) -> None:
    with open_database(_database_path(tmp_path)) as manager:
        with pytest.raises(TypeError):
            PeeweeJobRepository(manager.database)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            PeeweeCostReportRepository(manager.database)  # type: ignore[arg-type]
