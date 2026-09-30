"""Execute the documented quiescent manual procedure, not a backup feature.

Disposable real SQLite + published managed inputs/outputs; no user data root.
The manifest/checker below are test-only evidence, not a runtime backup API.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from image_fixtures import png_bytes

from aimedia.application.inputs import snapshot_reference_images
from aimedia.application.inputs.archive import archive_reference_images
from aimedia.artifacts import PillowArtifactStorage
from aimedia.artifacts.inputs import LocalManagedInputStorage
from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    Job,
    JobKind,
    ModelRef,
    ProviderRef,
)
from aimedia.storage import PeeweeJobRepository, open_database
from aimedia.storage.migrations import LATEST_SCHEMA_VERSION, current_schema_version


def _check_restored(root: Path, manifest: dict[str, object]) -> None:
    database = root / "database.sqlite3"
    if hashlib.sha256(database.read_bytes()).hexdigest() != manifest["database_sha256"]:
        raise ValueError("Database checksum mismatch")
    files = manifest["files"]
    assert isinstance(files, list)
    expected_paths = {entry["path"] for entry in files}
    actual_paths = {
        path.relative_to(root).as_posix()
        for name in ("inputs", "outputs")
        for path in (root / name).rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise ValueError("Managed file inventory mismatch")
    for entry in files:
        path = root / entry["path"]
        if path.is_symlink() or path.is_junction():
            raise ValueError("Redirected backup file")
        content = path.read_bytes()
        if (
            len(content) != entry["size_bytes"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Managed checksum/size mismatch")
    with open_database(database) as restored:
        assert (
            current_schema_version(restored.database)
            == manifest["schema_version"]
            == LATEST_SCHEMA_VERSION
        )
        jobs = PeeweeJobRepository(restored).list_recent()
        refs = [ref for job in jobs for ref in job.inputs if ref.managed_path is not None]
        artifacts = [
            artifact
            for job in jobs
            for artifact in job.artifacts
            if artifact.local_path is not None and not artifact.local_path.is_absolute()
        ]
        assert {ref.managed_path.as_posix() for ref in refs} | {
            item.local_path.as_posix() for item in artifacts
        } == expected_paths
        for ref in refs:
            LocalManagedInputStorage(data_root=root).resolve_path(ref)
        for artifact in artifacts:
            content = PillowArtifactStorage(data_root=root).resolve_path(artifact).read_bytes()
            assert len(content) == artifact.size_bytes
            assert hashlib.sha256(content).hexdigest() == artifact.sha256


@pytest.mark.parametrize(
    "damage",
    ["none", "missing_input", "bad_input_hash", "missing_output", "bad_output_size", "extra_temp"],
)
def test_manual_quiescent_backup_manifest_restore(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "original"
    root.mkdir()
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes())
    snapshots = snapshot_reference_images([source])
    request = ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id="test"),
        prompt=CompiledPrompt(text="synthetic", source_count=1),
        images=[snapshots[0].ref],
    )
    with open_database(root / "database.sqlite3") as manager:
        repository = PeeweeJobRepository(manager)
        job = repository.save(
            Job(
                kind=JobKind.IMAGE_GENERATE,
                provider=request.provider,
                model=request.model,
                request=request,
                inputs=request.images,
                created_at=datetime(2026, 10, 3, tzinfo=UTC),
            )
        )

        # Manual procedure tests publication/backup, not model support; validator
        # checks this synthetic request only, without activating any catalog model.
        def validate_synthetic(candidate: ImageGenerationRequest) -> None:
            assert candidate == request

        archive_reference_images(
            job,
            snapshots,
            repository=repository,
            storage=LocalManagedInputStorage(data_root=root),
            validate_model=validate_synthetic,
        )
        job = repository.get(job.id)
        output = PillowArtifactStorage(data_root=root).save(job_id=job.id, content=png_bytes())
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        external = PillowArtifactStorage(data_root=root).save(
            job_id=job.id, content=png_bytes(), output_dir=external_dir
        )
        job = repository.save(job.model_copy(update={"artifacts": (output, external)}))
        # Legacy provenance stays metadata, not a backfilled file.
        legacy = repository.save(
            Job(
                kind=JobKind.IMAGE_GENERATE,
                provider=request.provider,
                model=request.model,
                request=request,
                inputs=request.images,
                created_at=datetime(2026, 10, 3, tzinfo=UTC),
            )
        )
        schema_version = current_schema_version(manager.database)
    # All writers/connections closed; snapshot DB with fresh SQLite backup API.
    backup = tmp_path / "backup"
    backup.mkdir()
    with (
        sqlite3.connect(root / "database.sqlite3") as source_db,
        sqlite3.connect(backup / "database.sqlite3") as destination_db,
    ):
        source_db.backup(destination_db)
    for name in ("inputs", "outputs"):
        shutil.copytree(root / name, backup / name)
    entries = [
        {"path": ref.managed_path.as_posix(), "size_bytes": ref.size_bytes, "sha256": ref.sha256}
        for ref in job.inputs
    ]
    entries.append(
        {
            "path": output.local_path.as_posix(),
            "size_bytes": output.size_bytes,
            "sha256": output.sha256,
        }
    )
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "database_method": "sqlite_online_backup_api_quiescent",
        "database_sha256": hashlib.sha256((backup / "database.sqlite3").read_bytes()).hexdigest(),
        "schema_version": schema_version,
        "managed_file_count": len(entries),
        "files": entries,
        "excluded_remote_only": 0,
        "excluded_external_outputs": 1,
        "legacy_inputs_without_bytes": 1,
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    source.unlink()
    # Isolated restore uses the persisted manifest, not cached original objects.
    restored_root = tmp_path / "restore"
    shutil.copytree(backup, restored_root)
    restored_manifest = json.loads((restored_root / "manifest.json").read_text(encoding="utf-8"))
    copied_input = restored_root / entries[0]["path"]
    copied_output = restored_root / entries[1]["path"]
    if damage == "missing_input":
        copied_input.unlink()
    elif damage == "bad_input_hash":
        content = copied_input.read_bytes()
        copied_input.write_bytes(b"x" + content[1:])
    elif damage == "missing_output":
        copied_output.unlink()
    elif damage == "bad_output_size":
        copied_output.write_bytes(copied_output.read_bytes() + b"x")
    elif damage == "extra_temp":
        (restored_root / "inputs" / ".unresolved.part").write_bytes(b"unfinished")
    if damage == "none":
        _check_restored(restored_root, restored_manifest)
        with open_database(restored_root / "database.sqlite3") as manager:
            repo = PeeweeJobRepository(manager)
            assert repo.get(job.id) == job
            assert repo.get(legacy.id) == legacy
            assert repo.get(legacy.id).inputs[0].managed_path is None
        assert restored_manifest["managed_file_count"] == 2
        assert not (restored_root / "external").exists()
    else:
        with pytest.raises(ValueError):
            _check_restored(restored_root, restored_manifest)
