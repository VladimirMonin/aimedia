"""FTS migration preserves accepted schemas and updates atomically with history."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aimedia.application.prompts.compile import PromptCompiler, inline_source
from aimedia.domain.errors import InvalidParameterValueError
from aimedia.domain.job import Job
from aimedia.domain.refs import ModelRef, ProviderRef
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import MigrationFailedError
from aimedia.storage.migrations import MIGRATIONS, Migration, apply_migrations
from aimedia.storage.repository import PeeweeJobRepository
from aimedia.storage.search import HistorySearch


def make_job(text):
    prompt = PromptCompiler().compile([inline_source(text)])
    request = ImageGenerationRequest(
        provider=ProviderRef(id="polza"), model=ModelRef(id="history-model"), prompt=prompt.compiled
    )
    return Job(
        kind=request.kind,
        provider=request.provider,
        model=request.model,
        request=request,
        compiled_prompt=prompt.compiled,
        prompt_sources=list(prompt.sources),
        created_at=datetime.now(UTC),
    )


def test_v2_to_v3_backfill_and_parameterized_lexical_queries_without_legacy_reads(tmp_path: Path):
    manager = DatabaseManager(tmp_path / "history.sqlite3")
    manager.connect()
    try:
        apply_migrations(manager.database, MIGRATIONS[:2])
        repo = PeeweeJobRepository(manager)
        first = repo.save(make_job("laboratory blue robot"))
        second = repo.save(make_job("forest green bird"))
        before = manager.database.execute_sql(
            "SELECT name,sql FROM sqlite_master "
            "WHERE name IN ('jobs','inputs','managed_input_copies') ORDER BY name"
        ).fetchall()
        outcome = apply_migrations(manager.database)
        assert outcome.applied == (3,)
        assert (
            before
            == manager.database.execute_sql(
                "SELECT name,sql FROM sqlite_master "
                "WHERE name IN ('jobs','inputs','managed_input_copies') ORDER BY name"
            ).fetchall()
        )
        search = HistorySearch(manager, repo)
        assert [job.id for job in search.search("laboratory robot")] == [first.id]
        assert [job.id for job in search.search("green")] == [second.id]
        assert not search.search("robot' OR 1=1 --")
        assert not search.search('" AND NOT *')
        changed = repo.save(make_job("another robot"))
        assert [job.id for job in search.search("robot")] == [changed.id, first.id]
        assert apply_migrations(manager.database).applied == ()
        with pytest.raises(InvalidParameterValueError):
            search.search("  ")
        assert repo.get(first.id).compiled_prompt.text == "laboratory blue robot"
    finally:
        manager.close()


def test_fts_migration_and_index_update_rollback_are_not_partial(tmp_path):
    manager = DatabaseManager(tmp_path / "history.sqlite3")
    manager.connect()
    try:
        apply_migrations(manager.database, MIGRATIONS[:2])
        repo = PeeweeJobRepository(manager)
        job = repo.save(make_job("durable robot"))

        def fail_after_index(database):
            MIGRATIONS[2].apply(database)
            raise RuntimeError("synthetic migration failure")

        with pytest.raises(MigrationFailedError):
            apply_migrations(
                manager.database, (*MIGRATIONS[:2], Migration(3, "failed_fts", fail_after_index))
            )
        assert "jobs_fts" not in manager.database.get_tables()
        assert repo.get(job.id) == job
        apply_migrations(manager.database)
        with pytest.raises(RuntimeError), manager.database.atomic():
            manager.database.execute_sql(
                "UPDATE jobs SET compiled_prompt=? WHERE id=?", ("transient bird", job.id)
            )
            raise RuntimeError("rollback")
        assert [item.id for item in HistorySearch(manager, repo).search("robot")] == [job.id]
        assert not HistorySearch(manager, repo).search("transient")
    finally:
        manager.close()


def test_v3_db_only_backfill_ref_result_metadata_update_resave_and_deletion(tmp_path):
    from aimedia.domain.artifacts import Artifact, ArtifactKind
    from aimedia.domain.inputs import InputKind, InputRef
    from aimedia.domain.state import JobStatus

    source = tmp_path / "missing_legacy_cobalt_reference.png"
    artifact_path = tmp_path / "missing_legacy_tangerine_result.png"
    legacy_ref = InputRef(
        kind=InputKind.IMAGE,
        path=source,
        position=0,
        sha256="b" * 64,
        mime_type="image/png",
        size_bytes=123,
        metadata={"label": "cerulean_label", "width": 11},
    )
    legacy_artifact = Artifact(
        kind=ArtifactKind.IMAGE,
        local_path=artifact_path,
        sha256="c" * 64,
        mime_type="image/png",
        size_bytes=456,
        metadata={"label": "vermilion_label", "width": 22},
    )
    base = make_job("plain robot")
    snapshot = Job.model_validate(
        {**base.model_dump(), "inputs": [legacy_ref], "artifacts": [legacy_artifact]}
    )
    path = tmp_path / "history.sqlite3"
    manager = DatabaseManager(path)
    manager.connect()
    try:
        apply_migrations(manager.database, MIGRATIONS[:2])
        repo = PeeweeJobRepository(manager)
        job = repo.save(snapshot)
        before = repo.get(job.id)
        apply_migrations(manager.database)
        assert repo.get(job.id) == before
        assert not source.exists() and not artifact_path.exists()
        search = HistorySearch(manager, repo)
        for query in (
            source.name,
            source.as_posix(),
            legacy_ref.sha256,
            "cerulean_label",
            artifact_path.name,
            artifact_path.as_posix(),
            legacy_artifact.sha256,
            "vermilion_label",
        ):
            assert [item.id for item in search.search(query)] == [job.id]
        assert not search.search("cobalt", status=JobStatus.COMPLETED)
        assert not search.search("cobalt' OR 1=1 --")
        assert not search.search("not_in_any_snapshot")
        managed = InputRef.model_validate(
            {**legacy_ref.model_dump(), "managed_path": f"inputs/{job.id}/000.png"}
        )
        job = repo.save(Job.model_validate({**job.model_dump(), "inputs": [managed]}))
        assert [item.id for item in search.search(managed.managed_path.as_posix())] == [job.id]
        assert repo.save(job) == job
        assert manager.database.execute_sql("SELECT count(*) FROM jobs_fts").fetchone() == (1,)
        changed_ref = InputRef.model_validate(
            {
                **managed.model_dump(),
                "path": tmp_path / "changed_indigo.png",
                "sha256": "d" * 64,
                "metadata": {},
            }
        )
        changed = repo.save(
            Job.model_validate({**job.model_dump(), "inputs": [changed_ref], "artifacts": []})
        )
        assert not search.search("cobalt") and not search.search("cerulean_label")
        assert not search.search("tangerine") and not search.search("vermilion_label")
        assert not search.search(legacy_ref.sha256) and not search.search(legacy_artifact.sha256)
        assert [item.id for item in search.search("indigo")] == [changed.id]
        assert repo.save(changed) == changed
    finally:
        manager.close()
    with DatabaseManager(path) as reopened:
        repo = PeeweeJobRepository(reopened)
        search = HistorySearch(reopened, repo)
        assert [item.id for item in search.search("indigo")] == [changed.id]
        reopened.database.execute_sql("DELETE FROM jobs WHERE id=?", (changed.id,))
        assert not search.search("indigo")
        assert reopened.database.execute_sql("SELECT count(*) FROM jobs_fts").fetchone() == (0,)
