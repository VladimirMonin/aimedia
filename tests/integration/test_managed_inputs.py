"""CN-01 public FS/SQLite seam on disposable roots; no live provider or CLI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import sys
import traceback
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fake_provider import FakeImageProvider
from image_fixtures import jpeg_bytes, png_bytes, webp_lossless_bytes

from aimedia.application.inputs import (
    ReferenceSnapshot,
    prepare_reference_images,
    snapshot_reference_images,
)
from aimedia.application.inputs.archive import InputArchiveError, archive_reference_images
from aimedia.artifacts.inputs import LocalManagedInputStorage
from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    InputFileNotFoundError,
    Job,
    JobKind,
    JobStatus,
    ModelRef,
    ProviderRef,
)
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.registry import (
    CapabilityNode,
    InputLimit,
    ModelResolver,
    ProviderBinding,
    validate_model_request,
)
from aimedia.registry.models import ModelRecord
from aimedia.storage import PeeweeJobRepository, open_database


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[PeeweeJobRepository]:
    (tmp_path / "data").mkdir()
    with open_database(tmp_path / "data" / "database.sqlite3") as manager:
        yield PeeweeJobRepository(manager)


def _snapshots(tmp_path: Path) -> tuple[ReferenceSnapshot, ...]:
    # Outside data-root; suffix is deliberately wrong, naming uses actual MIME.
    paths = [tmp_path / "source.jpg", tmp_path / "second.png"]
    for path, content in zip(paths, (png_bytes(), jpeg_bytes()), strict=True):
        path.write_bytes(content)
    return snapshot_reference_images(paths)


def _job(repository: PeeweeJobRepository, snapshots: tuple[ReferenceSnapshot, ...]) -> Job:
    request = ImageGenerationRequest(
        provider=ProviderRef(id="polza"),
        model=ModelRef(id="synthetic-copy-model"),
        prompt=CompiledPrompt(text="synthetic", source_count=1),
        images=[snapshot.ref for snapshot in snapshots],
    )
    return repository.save(
        Job(
            kind=JobKind.IMAGE_GENERATE,
            provider=request.provider,
            model=request.model,
            request=request,
            inputs=request.images,
            created_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
    )


def _validate(request: ImageGenerationRequest) -> object:
    record = ModelRecord(
        schema_version=1,
        model_id="synthetic-copy-model",
        name="Synthetic copy model",
        family="image",
        status="active",
        capabilities={"reference_images": CapabilityNode(supported=True, max=4)},
        inputs={"images": InputLimit(supported=True, max=4, formats=("png", "jpeg", "webp"))},
        outputs={},
        parameters={},
        providers={"polza": ProviderBinding(remote_model_id="synthetic/copy")},
    )
    effective = ModelResolver([record]).resolve(record.model_id, "polza")
    validate_model_request(effective, request)
    return effective


def _archive(
    job: Job, snapshots: tuple[ReferenceSnapshot, ...], repository: PeeweeJobRepository, root: Path
) -> ImageGenerationRequest:
    return archive_reference_images(
        job,
        snapshots,
        repository=repository,
        storage=LocalManagedInputStorage(data_root=root),
        validate_model=_validate,
    )


def _attempt(
    job: Job,
    snapshots: tuple[ReferenceSnapshot, ...],
    repository: PeeweeJobRepository,
    root: Path,
    gateway: FakeImageProvider,
) -> None:
    # Public composed caller: a failed prerequisite cannot reach submit.
    request = _archive(job, snapshots, repository, root)
    asyncio.run(gateway.submit(request))


@pytest.mark.parametrize("changed", ["deleted", "mutated"])
def test_snapshot_once_copies_exact_bytes_after_source_change(
    tmp_path: Path,
    repository: PeeweeJobRepository,
    changed: str,
) -> None:
    reads: list[Path] = []
    original_read = Path.read_bytes

    def counted(path: Path) -> bytes:
        reads.append(path)
        return original_read(path)

    with patch.object(Path, "read_bytes", counted):
        snapshots = _snapshots(tmp_path)
    assert reads == [snapshot.ref.path for snapshot in snapshots]
    assert "content=" not in repr(snapshots[0])
    job = _job(repository, snapshots)
    for snapshot in snapshots:
        if changed == "deleted":
            snapshot.ref.path.unlink()
        else:
            snapshot.ref.path.write_bytes(b"not the prepared image")
    transport = _archive(job, snapshots, repository, tmp_path / "data")
    saved = repository.get(job.id)
    assert saved is not None
    assert saved.request.images == saved.inputs
    assert saved.artifacts == () and saved.result is None
    for index, (snapshot, ref, sent) in enumerate(
        zip(snapshots, saved.inputs, transport.images, strict=True)
    ):
        assert ref.path == snapshot.ref.path
        assert ref.position == index
        assert ref.managed_path == Path(
            f"inputs/{job.id}/{index}.{'png' if index == 0 else 'jpeg'}"
        )
        assert ref.sha256 == hashlib.sha256(snapshot.content).hexdigest()
        assert ref.size_bytes == len(snapshot.content)
        assert sent.path.is_absolute() and sent.path.read_bytes() == snapshot.content
        assert sent.managed_path == ref.managed_path
    assert repository.save(saved) == saved
    assert repository.list_recent()[0] == saved
    assert job.inputs == [snapshot.ref for snapshot in snapshots]


def test_reopen_and_resave_preserve_copy_links(tmp_path: Path) -> None:
    snapshots = _snapshots(tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    with open_database(root / "database.sqlite3") as manager:
        repo = PeeweeJobRepository(manager)
        job = _job(repo, snapshots)
        _archive(job, snapshots, repo, root)
        expected = repo.get(job.id)
    for snapshot in snapshots:
        snapshot.ref.path.unlink()
    with open_database(root / "database.sqlite3") as reopened:
        repo = PeeweeJobRepository(reopened)
        assert repo.get(job.id) == expected
        assert repo.save(expected) == expected
        for ref in expected.inputs:
            assert LocalManagedInputStorage(data_root=root).resolve_path(ref).read_bytes()


@pytest.mark.parametrize(
    "content,mime,extension",
    [
        (png_bytes(), "image/png", "png"),
        (jpeg_bytes(), "image/jpeg", "jpeg"),
        (webp_lossless_bytes(), "image/webp", "webp"),
    ],
)
def test_copy_mime_and_metadata_api_reuse(
    tmp_path: Path, content: bytes, mime: str, extension: str
) -> None:
    source = tmp_path / "misnamed.bin"
    source.write_bytes(content)
    snapshots = snapshot_reference_images([source])
    assert prepare_reference_images([source]) == tuple(item.ref for item in snapshots)
    storage = LocalManagedInputStorage(data_root=tmp_path / "data")
    copied = storage.save(job_id=7, ref=snapshots[0].ref, content=content)
    assert copied.mime_type == mime
    assert copied.managed_path == Path(f"inputs/7/0.{extension}")
    assert storage.resolve_path(copied).read_bytes() == content


def test_conflicts_are_suffixed_not_overwritten_or_deleted(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    root = tmp_path / "data"
    directory = root / "inputs" / str(job.id)
    directory.mkdir(parents=True)
    for name in ("0.png", "0_002.png", "unrelated.txt"):
        (directory / name).write_bytes(b"user-owned")
    sent = _archive(job, snapshots, repository, root)
    assert sent.images[0].managed_path == Path(f"inputs/{job.id}/0_003.png")
    for name in ("0.png", "0_002.png", "unrelated.txt"):
        assert (directory / name).read_bytes() == b"user-owned"
    assert snapshots[0].ref.path.read_bytes() == snapshots[0].content
    assert not list(root.rglob("*.part"))


@pytest.mark.parametrize(
    "bad",
    [
        "unknown_id",
        "no_id",
        "bool_id",
        "zero_id",
        "negative_id",
        "running",
        "inputs",
        "request",
        "position",
        "bytes",
        "existing_copy",
    ],
)
def test_preflight_failures_do_not_publish_or_submit(
    tmp_path: Path, repository: PeeweeJobRepository, bad: str
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    if bad == "unknown_id":
        job = job.model_copy(update={"id": 999})
    elif bad == "no_id":
        job = job.model_copy(update={"id": None})
    elif bad in {"bool_id", "zero_id", "negative_id"}:
        job = job.model_copy(update={"id": {"bool_id": True, "zero_id": 0, "negative_id": -1}[bad]})
    elif bad == "running":
        job = job.model_copy(update={"status": JobStatus.RUNNING})
    elif bad == "inputs":
        job = job.model_copy(update={"inputs": job.inputs[:1]})
    elif bad == "request":
        job = job.model_copy(update={"request": job.request.model_copy(update={"images": []})})
    elif bad == "position":
        snapshots = (
            replace(snapshots[0], ref=snapshots[0].ref.model_copy(update={"position": -1})),
            *snapshots[1:],
        )
    elif bad == "bytes":
        snapshots = (replace(snapshots[0], content=png_bytes(3, 3)), *snapshots[1:])
    else:
        snapshots = (
            replace(
                snapshots[0],
                ref=snapshots[0].ref.model_copy(update={"managed_path": Path("inputs/1/0.png")}),
            ),
            *snapshots[1:],
        )
    gateway = FakeImageProvider()
    with pytest.raises(ValueError):
        _attempt(job, snapshots, repository, tmp_path / "data", gateway)
    assert gateway.submit_count == 0
    assert not (tmp_path / "data" / "inputs").exists()


def test_model_validation_after_confirmed_id_before_publication(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    root = tmp_path / "data"
    seen = []

    def reject(request: ImageGenerationRequest) -> None:
        assert repository.get(job.id) == job
        assert not (root / "inputs").exists()
        seen.append(request)
        raise ValueError("model validation rejected")

    gateway = FakeImageProvider()
    with pytest.raises(ValueError):
        request = archive_reference_images(
            job,
            snapshots,
            repository=repository,
            storage=LocalManagedInputStorage(data_root=root),
            validate_model=reject,
        )
        asyncio.run(gateway.submit(request))
    assert seen == [job.request] and gateway.submit_count == 0
    assert repository.get(job.id) == job


@pytest.mark.parametrize(
    "field,value", [("sha256", "a" * 64), ("size_bytes", 1), ("mime_type", "image/jpeg")]
)
def test_adapter_rejects_mismatched_metadata_before_mkdir(
    tmp_path: Path, field: str, value: object
) -> None:
    snapshot = _snapshots(tmp_path)[0]
    storage = LocalManagedInputStorage(data_root=tmp_path / "data")
    with pytest.raises(ValueError):
        storage.save(
            job_id=1, ref=snapshot.ref.model_copy(update={field: value}), content=snapshot.content
        )
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("fault", ["write", "fsync", "link", "second_copy"])
def test_publication_failure_preserves_files_and_blocks_submit(
    tmp_path: Path, repository: PeeweeJobRepository, fault: str
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    original_link = os.link
    calls = 0

    def broken_link(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if fault == "link" or calls == 2:
            raise OSError("unsafe path canary")
        original_link(source, destination)

    gateway = FakeImageProvider()
    target = "aimedia.artifacts.output.os." + (fault if fault in {"write", "fsync"} else "link")
    kwargs = (
        {"side_effect": OSError("unsafe path canary")}
        if fault in {"write", "fsync"}
        else {"side_effect": broken_link}
    )
    with patch(target, **kwargs), pytest.raises(InputArchiveError) as caught:
        _attempt(job, snapshots, repository, tmp_path / "data", gateway)
    assert gateway.submit_count == 0
    assert repository.get(job.id) == job
    assert not list((tmp_path / "data").rglob("*.part"))
    files = list((tmp_path / "data" / "inputs").rglob("*.png"))
    assert len(files) == (1 if fault == "second_copy" else 0)
    if files:
        assert files[0].read_bytes() == snapshots[0].content
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "canary" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "fault",
    [
        "before_commit",
        "after_commit",
        "partial_commit",
        "wrong_return",
        "unreadable_after_commit",
        "wrong_read",
        "wrong_hash",
        "wrong_size",
        "wrong_path",
    ],
)
def test_db_confirmation_no_retry_or_cleanup(
    tmp_path: Path, repository: PeeweeJobRepository, fault: str
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    original_save = repository.save
    original_get = repository.get
    saves = 0
    committed = False

    def save(archived: Job) -> Job:
        nonlocal saves, committed
        saves += 1
        if fault == "before_commit":
            raise RuntimeError("DB canary")
        if fault == "partial_commit":
            partial = archived.model_copy(update={"inputs": archived.inputs[:1]})
            original_save(partial)
        else:
            original_save(archived)
        committed = True
        if fault == "wrong_return":
            return archived.model_copy(update={"id": 999})
        if fault in {"wrong_read", "wrong_hash", "wrong_size", "wrong_path"}:
            return archived
        raise RuntimeError("DB canary")

    def get(job_id: int) -> Job | None:
        if committed and fault == "unreadable_after_commit":
            raise RuntimeError("DB read canary")
        loaded = original_get(job_id)
        if committed and loaded is not None:
            if fault == "wrong_read":
                return loaded.model_copy(update={"inputs": loaded.inputs[:1]})
            if fault in {"wrong_hash", "wrong_size", "wrong_path"}:
                changes = {
                    "wrong_hash": {"sha256": "a" * 64},
                    "wrong_size": {"size_bytes": 1},
                    "wrong_path": {"managed_path": Path(f"inputs/{job_id}/0_999.png")},
                }
                ref = loaded.inputs[0].model_copy(update=changes[fault])
                return loaded.model_copy(update={"inputs": [ref, *loaded.inputs[1:]]})
        return loaded

    gateway = FakeImageProvider()
    with patch.object(repository, "save", save), patch.object(repository, "get", get):
        if fault == "after_commit":
            _attempt(job, snapshots, repository, tmp_path / "data", gateway)
            assert gateway.submit_count == 1
        else:
            with pytest.raises(InputArchiveError):
                _attempt(job, snapshots, repository, tmp_path / "data", gateway)
            assert gateway.submit_count == 0
    assert saves == 1
    assert len(list((tmp_path / "data" / "inputs").rglob("*.*"))) == 2
    assert all(snapshot.ref.path.exists() for snapshot in snapshots)
    if fault == "before_commit":
        assert repository.get(job.id) == job


@pytest.mark.parametrize(
    "tamper", ["missing", "changed", "traversal", "foreign_job", "absolute", "symlink"]
)
def test_copy_resolve_fails_closed(tmp_path: Path, tamper: str) -> None:
    snapshot = _snapshots(tmp_path)[0]
    storage = LocalManagedInputStorage(data_root=tmp_path / "data")
    ref = storage.save(job_id=1, ref=snapshot.ref, content=snapshot.content)
    path = storage.resolve_path(ref)
    if tamper in {"missing", "symlink"}:
        path.unlink()
        if tamper == "symlink":
            try:
                path.symlink_to(snapshot.ref.path)
            except OSError:
                # Windows without symlink permission still exercises redirect guard below.
                with (
                    patch.object(Path, "is_symlink", lambda candidate: candidate == path),
                    pytest.raises(ValueError),
                ):
                    storage.resolve_path(ref)
                return
    elif tamper == "changed":
        path.write_bytes(png_bytes(4, 4))
    else:
        bad_path = {
            "traversal": "inputs/1/../0.png",
            "foreign_job": "outputs/1/0.png",
            "absolute": str(path),
        }[tamper]
        ref = ref.model_copy(update={"managed_path": bad_path})
    with pytest.raises(ValueError):
        storage.resolve_path(ref)
    assert snapshot.ref.path.read_bytes() == snapshot.content


def test_oversized_tampered_copy_rejected_before_read(tmp_path: Path) -> None:
    snapshot = _snapshots(tmp_path)[0]
    storage = LocalManagedInputStorage(data_root=tmp_path / "data")
    ref = storage.save(job_id=1, ref=snapshot.ref, content=snapshot.content)
    path = storage.resolve_path(ref)
    path.write_bytes(snapshot.content + b"x" * (512 * 1024))
    with patch.object(Path, "open", side_effect=AssertionError("Unexpected copy read")):
        with pytest.raises(ValueError, match="size mismatch"):
            storage.resolve_path(ref)
    assert path.exists()


@pytest.mark.parametrize("redirect", ["symlink", "junction"])
@pytest.mark.parametrize("phase", ["publish", "read"])
def test_existing_ancestor_redirect_rejected(tmp_path: Path, redirect: str, phase: str) -> None:
    snapshot = _snapshots(tmp_path)[0]
    root = tmp_path / "data"
    storage = LocalManagedInputStorage(data_root=root)
    ancestor = root / "inputs"
    if phase == "read":
        ref = storage.save(job_id=1, ref=snapshot.ref, content=snapshot.content)
    # Deterministic junction/symlink signal even on platforms lacking creation privileges.
    with (
        patch.object(Path, f"is_{redirect}", lambda candidate: candidate == ancestor),
        pytest.raises(ValueError),
    ):
        if phase == "publish":
            storage.save(job_id=1, ref=snapshot.ref, content=snapshot.content)
        else:
            storage.resolve_path(ref)
    assert (root / "inputs").exists() == (phase == "read")


def test_real_symlink_ancestor_cannot_write_outside(tmp_path: Path) -> None:
    snapshot = _snapshots(tmp_path)[0]
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "inputs").symlink_to(outside, target_is_directory=True)
    except OSError:
        with (
            patch.object(Path, "is_symlink", lambda candidate: candidate == root / "inputs"),
            pytest.raises(ValueError),
        ):
            LocalManagedInputStorage(data_root=root).save(
                job_id=1, ref=snapshot.ref, content=snapshot.content
            )
        return
    with pytest.raises(ValueError):
        LocalManagedInputStorage(data_root=root).save(
            job_id=1, ref=snapshot.ref, content=snapshot.content
        )
    assert list(outside.iterdir()) == []


def test_real_junction_ancestor_on_windows(tmp_path: Path) -> None:
    if sys.platform != "win32":
        # Junctions are Windows-specific; real POSIX redirect tested separately.
        return
    snapshot = _snapshots(tmp_path)[0]
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    importlib.import_module("_winapi").CreateJunction(str(outside), str(root / "inputs"))
    assert (root / "inputs").is_junction()
    with pytest.raises(ValueError):
        LocalManagedInputStorage(data_root=root).save(
            job_id=1, ref=snapshot.ref, content=snapshot.content
        )
    assert list(outside.iterdir()) == []


def test_tampered_copy_after_commit_blocks_ready_and_submit(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    original_save = repository.save

    def save(archived: Job) -> Job:
        saved = original_save(archived)
        (tmp_path / "data" / saved.inputs[0].managed_path).write_bytes(b"tampered")
        return saved

    gateway = FakeImageProvider()
    with patch.object(repository, "save", save), pytest.raises(InputArchiveError):
        _attempt(job, snapshots, repository, tmp_path / "data", gateway)
    assert gateway.submit_count == 0
    assert repository.get(job.id).inputs[0].managed_path is not None


def test_positive_polza_mock_http_reads_copies_after_sources_gone(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    for snapshot in snapshots:
        snapshot.ref.path.unlink()
    transport = _archive(job, snapshots, repository, tmp_path / "data")
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        assert body["input"]["images"] == [
            {
                "type": "base64",
                "data": f"data:{item.ref.mime_type};base64,"
                + base64.b64encode(item.content).decode(),
            }
            for item in snapshots
        ]
        return httpx.Response(
            200,
            json={"id": "synthetic-copy-task", "object": "media.generation", "status": "pending"},
        )

    async def submit() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = PolzaProviderGateway(
                client=client,
                api_key="synthetic-test-key",
                effective=_validate(transport),
                max_body_bytes=1024 * 1024,
                max_response_bytes=1024 * 1024,
            )
            await gateway.submit(transport)

    asyncio.run(submit())
    assert len(calls) == 1 and calls[0].method == "POST"
    assert repository.get(job.id).inputs[0].path == snapshots[0].ref.path


def test_real_transaction_rollback_keeps_published_copies(
    tmp_path: Path,
    repository: PeeweeJobRepository,
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    original_replace = repository._replace_children

    def fail_inside_transaction(record: object, saved: Job) -> None:
        original_replace(record, saved)
        raise RuntimeError("rollback canary")

    gateway = FakeImageProvider()
    with (
        patch.object(repository, "_replace_children", fail_inside_transaction),
        pytest.raises(InputArchiveError),
    ):
        _attempt(job, snapshots, repository, tmp_path / "data", gateway)
    assert repository.get(job.id) == job
    assert gateway.submit_count == 0
    assert len(list((tmp_path / "data" / "inputs").rglob("*.*"))) == 2


def test_cannot_republish_confirmed_archive(
    tmp_path: Path,
    repository: PeeweeJobRepository,
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    _archive(job, snapshots, repository, tmp_path / "data")
    archived = repository.get(job.id)
    gateway = FakeImageProvider()
    with pytest.raises(ValueError):
        _attempt(archived, snapshots, repository, tmp_path / "data", gateway)
    assert gateway.submit_count == 0
    assert len(list((tmp_path / "data" / "inputs").rglob("*.*"))) == 2


def test_legacy_history_has_no_copy_and_no_backfill(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    snapshots = _snapshots(tmp_path)
    job = _job(repository, snapshots)
    for snapshot in snapshots:
        snapshot.ref.path.unlink()
    loaded = repository.get(job.id)
    assert loaded == job and all(ref.managed_path is None for ref in loaded.inputs)
    assert repository.save(loaded) == job
    assert not (tmp_path / "data" / "inputs").exists()


def test_source_read_failure_precedes_job_creation(
    tmp_path: Path, repository: PeeweeJobRepository
) -> None:
    with pytest.raises(InputFileNotFoundError):
        snapshots = snapshot_reference_images([tmp_path / "missing.png"])
        _job(repository, snapshots)
    assert repository.list_recent() == []
