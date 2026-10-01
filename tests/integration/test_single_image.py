"""Public E07 use case with real disposable SQLite, managed copies and Pillow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import httpx
import pytest
from fake_provider import FakeImageProvider, FakeScenario
from image_fixtures import png_bytes

from aimedia.application.execution import ImageServices, sync_image
from aimedia.application.inputs import snapshot_reference_images
from aimedia.application.prompts.compile import PromptCompiler, file_source, inline_source
from aimedia.application.single_image import ImageExecutionSetup, ImageHistoryError, generate_image
from aimedia.artifacts import PillowArtifactStorage
from aimedia.artifacts.inputs import LocalManagedInputStorage
from aimedia.domain import (
    ArtifactKind,
    Cost,
    ImageGenerationRequest,
    InputFileNotFoundError,
    InvalidParameterValueError,
    JobError,
    JobStatus,
    ModelRef,
    ProviderError,
    ProviderJobState,
    ProviderModelBinding,
    ProviderRef,
    ProviderResult,
    RemoteArtifact,
    RemoteJobRef,
    RemoteOperation,
    SubmissionResult,
    UnknownModelError,
    Usage,
)
from aimedia.logging import EventLogger
from aimedia.providers.polza.gateway import PolzaProviderGateway
from aimedia.registry import (
    CapabilityNode,
    InputLimit,
    ModelResolver,
    ProviderBinding,
    validate_model_request,
)
from aimedia.registry.builtin import load_builtin_registry
from aimedia.registry.models import ModelRecord
from aimedia.storage import PeeweeJobRepository, open_database
from aimedia.storage.ownership import claim_job

CANARY = "sensitive_CANARY_e07_signed_body"
NOW = datetime(2026, 10, 3, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waits = []
        self.interrupt = None

    def monotonic(self):
        return self.value

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds
        if self.interrupt is not None:
            raise self.interrupt


class Provider(FakeImageProvider):
    def __init__(self, *, asynchronous=False, count=1, cost="4.800", **kwargs):
        super().__init__(FakeScenario.PENDING if asynchronous else FakeScenario.SUCCESS, **kwargs)
        self.result = ProviderResult(
            remote_artifacts=[
                RemoteArtifact(
                    kind=ArtifactKind.IMAGE,
                    url=f"https://s3.polza.ai/output/{index}.png?sig={CANARY}",
                )
                for index in range(count)
            ],
            cost=Cost(amount=cost, currency="RUB") if cost is not None else None,
            usage=Usage(output_units=count, raw={"images": count, "cost_rub": Decimal("4.800")}),
            provider_metadata={"model": "synthetic/image"},
        )
        self.first_poll = None

    def _completed_result(self):
        return self.result

    async def submit(self, request):
        returned = await super().submit(request)
        if returned.state is ProviderJobState.COMPLETED:
            return SubmissionResult(
                state=returned.state, result=returned.result, remote_ref=self.remote_ref()
            )
        return returned

    async def get_status(self, ref):
        if self.first_poll:
            self.first_poll(ref)
        return await super().get_status(ref)


class Repository:
    """Fault at a named real transaction boundary, without replacing real storage."""

    def __init__(self, real):
        self.real = real
        self.fail = None
        self.commit = False
        self.saves = []

    def save(self, job):
        self.saves.append(job)
        if self.fail and self.fail(job):
            if self.commit:
                self.real.save(job)
            raise RuntimeError(CANARY)
        return self.real.save(job)

    def get(self, job_id):
        return self.real.get(job_id)

    def list_recent(self, **kwargs):
        return self.real.list_recent(**kwargs)


class Rig:
    def __init__(self, root, repo, provider, *, refs=0):
        self.root = root
        self.repo = repo
        self.provider = provider
        self.clock = Clock()
        self.logs = StringIO()
        self.downloads = []
        self.sources_file = root.parent / "prompt.txt"
        self.sources_file.write_text("second", encoding="utf-8")
        self.prompt = PromptCompiler().compile(
            [inline_source("first"), file_source(self.sources_file)]
        )
        paths = []
        for index in range(refs):
            path = root.parent / f"source{index}.png"
            path.write_bytes(png_bytes())
            paths.append(path)
        self.snapshots = snapshot_reference_images(paths)
        self.request = ImageGenerationRequest(
            provider=ProviderRef(id=provider.provider_id),
            model=ModelRef(id="synthetic-image"),
            prompt=self.prompt.compiled,
            images=[s.ref for s in self.snapshots],
        )
        self.inputs = LocalManagedInputStorage(data_root=root)
        self.artifacts = PillowArtifactStorage(data_root=root)
        self.setup = self.prepare
        self.downloader = self.download

    def prepare(self, request):
        created = self.repo.list_recent()[0]
        assert created.status is JobStatus.CREATED and created.id > 0
        assert created.compiled_prompt == self.prompt.compiled
        return ImageExecutionSetup(
            binding=ProviderModelBinding(
                provider_id=request.provider.id, remote_model_id="synthetic/image"
            ),
            gateway=self.provider,
        )

    async def download(self, artifact):
        stored = self.repo.list_recent()[0]
        assert stored.cost == self.provider.result.cost
        assert stored.usage is not None
        assert CANARY not in stored.model_dump_json()
        self.downloads.append(artifact)
        return png_bytes()

    async def run(self, **kwargs):
        return await generate_image(
            self.request,
            self.snapshots,
            repository=self.repo,
            input_storage=self.inputs,
            artifact_storage=self.artifacts,
            prepare_provider=self.setup,
            download=self.downloader,
            prompt_sources=self.prompt.sources,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            now=lambda: NOW,
            logger=EventLogger(component="application.single_image", stream=self.logs),
            **kwargs,
        )


def make_rig(tmp_path, provider=None, refs=0):
    root = tmp_path / "data"
    root.mkdir()
    manager = open_database(root / "database.sqlite3")
    rig = Rig(root, Repository(PeeweeJobRepository(manager)), provider or Provider(), refs=refs)
    return rig, manager


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("refs", [0, 2])
def test_completed_reopens_with_prompt_copies_billing_and_verified_files(
    tmp_path, asynchronous, refs
):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=asynchronous), refs=refs)
    for snapshot in rig.snapshots:
        snapshot.ref.path.unlink()
    rig.sources_file.unlink()

    def first_poll(ref):
        stored = rig.repo.list_recent()[0]
        assert stored.remote_ref == ref and stored.submitted_at == NOW
        assert stored.status in (JobStatus.SUBMITTED, JobStatus.RUNNING)
        assert all(item.managed_path is not None for item in stored.inputs)

    rig.provider.first_poll = first_poll
    try:
        completed = asyncio.run(rig.run())
        assert completed.status is JobStatus.COMPLETED
        assert completed.compiled_prompt.text == "first\n\nsecond"
        assert completed.prompt_sources == list(rig.prompt.sources)
        assert completed.remote_model_id == "synthetic/image"
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
        assert rig.provider.fetch_count == int(asynchronous)
        if asynchronous:
            assert rig.clock.waits == [1.0, 1.0]
            running_writes = [j for j in rig.repo.saves if j.status is JobStatus.RUNNING]
            assert len(running_writes) == 2
            assert running_writes[0].usage is None
            assert running_writes[1].usage is not None
        for original, persisted, transport in zip(
            rig.snapshots, completed.inputs, rig.provider.submitted_requests[0].images, strict=True
        ):
            assert persisted.path == original.ref.path
            assert transport.path != persisted.path and transport.path.is_absolute()
            assert rig.inputs.resolve_path(persisted).read_bytes() == original.content
        artifact = completed.result.artifacts[0]
        content = rig.artifacts.resolve_path(artifact).read_bytes()
        assert artifact.sha256 == hashlib.sha256(content).hexdigest()
        assert artifact.size_bytes == len(content)
        events = [json.loads(line)["event"] for line in rig.logs.getvalue().splitlines()]
        assert events.index("usage_recorded") < events.index("artifact_saved")
        assert events[-1] == "job_completed"
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        stored = PeeweeJobRepository(reopened).get(completed.id)
        assert stored == completed
        assert stored.cost.amount == Decimal("4.800")
        assert CANARY not in stored.model_dump_json()
    assert CANARY.encode() not in (rig.root / "database.sqlite3").read_bytes()
    assert CANARY not in rig.logs.getvalue()


@pytest.mark.parametrize(
    "kind", ["model", "params", "setup", "archive", "created", "archive_confirm"]
)
def test_pre_submit_failures_never_post(tmp_path, kind):
    rig, manager = make_rig(tmp_path, refs=1)
    if kind in ("model", "params", "setup"):

        def failed(request):
            rig.prepare(request)
            if kind == "model":
                raise UnknownModelError(CANARY)
            if kind == "params":
                raise InvalidParameterValueError(CANARY)
            raise RuntimeError(CANARY)

        rig.setup = failed
    elif kind == "archive":
        rig.inputs.save = lambda **kwargs: (_ for _ in ()).throw(RuntimeError(CANARY))
    elif kind == "created":
        rig.repo.fail = lambda job: job.id is None
    else:
        rig.repo.fail = lambda job: (
            job.status is JobStatus.CREATED and any(ref.managed_path for ref in job.inputs)
        )
    try:
        if kind == "created":
            with pytest.raises(ImageHistoryError) as caught:
                asyncio.run(rig.run())
            assert CANARY not in str(caught.value)
            assert rig.repo.list_recent() == []
        else:
            failed = asyncio.run(rig.run())
            assert failed.status is JobStatus.FAILED
            assert CANARY not in failed.model_dump_json()
        assert rig.provider.submit_count == 0
        if kind == "archive_confirm":
            assert list((rig.root / "inputs").rglob("*.png"))
    finally:
        manager.close()


@pytest.mark.parametrize("running", [False, True])
def test_repeated_poll_observations_write_only_one_running_transition(tmp_path, running):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=True, pending_polls=3))
    original_submit = rig.provider.submit
    if running:

        async def submit(request):
            result = await original_submit(request)
            return result.model_copy(update={"state": ProviderJobState.RUNNING})

        rig.provider.submit = submit
    try:
        completed = asyncio.run(rig.run())
        assert completed.status is JobStatus.COMPLETED
        assert rig.clock.waits == [1.0] * 4
        transitions = [
            j for j in rig.repo.saves if j.status is JobStatus.RUNNING and j.usage is None
        ]
        assert len(transitions) == 1
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()


@pytest.mark.parametrize("interrupt", [asyncio.CancelledError(), KeyboardInterrupt()])
def test_interrupt_after_remote_result_preserves_actual_billing(tmp_path, interrupt):
    rig, manager = make_rig(tmp_path)

    async def download(locator):
        await rig.download(locator)
        raise interrupt

    rig.downloader = download
    try:
        with pytest.raises(type(interrupt)):
            asyncio.run(rig.run())
        job = rig.repo.list_recent()[0]
        assert job.error.code == "JOB_WAIT_INTERRUPTED"
        assert job.remote_ref == rig.provider.remote_ref()
        assert job.cost.amount == Decimal("4.800")
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()


def test_billing_history_failure_stops_before_download(tmp_path):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=True))
    rig.repo.fail = lambda job: job.usage is not None
    try:
        with pytest.raises(ImageHistoryError) as caught:
            asyncio.run(rig.run())
        assert caught.value.job.cost.amount == Decimal("4.800")
        assert caught.value.job.remote_ref == rig.provider.remote_ref()
        assert rig.downloads == [] and rig.provider.submit_count == 1
    finally:
        manager.close()


def test_published_file_must_be_verified_before_completed(tmp_path):
    rig, manager = make_rig(tmp_path)
    rig.artifacts.exists = lambda artifact: False
    try:
        with pytest.raises(ImageHistoryError) as caught:
            asyncio.run(rig.run())
        partial = caught.value.job
        assert rig.artifacts.resolve_path(partial.result.artifacts[0]).is_file()
        assert rig.repo.get(partial.id).status is JobStatus.SUBMITTED
        assert partial.cost.amount == Decimal("4.800")
        assert rig.provider.submit_count == 1
    finally:
        manager.close()


def test_read_failure_creates_no_job(tmp_path):
    rig, manager = make_rig(tmp_path)
    try:
        with pytest.raises(InputFileNotFoundError):
            snapshot_reference_images([tmp_path / "missing.png"])
        assert rig.repo.list_recent() == [] and rig.provider.submit_count == 0
    finally:
        manager.close()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(CANARY),
        ProviderError(
            JobError(
                code="SUBMIT_UNCERTAIN",
                message=CANARY,
                provider_message=CANARY,
                details={"Authorization": CANARY},
                retryable=None,
            )
        ),
    ],
)
def test_lost_submit_response_is_one_attempt_and_unknown(tmp_path, error):
    rig, manager = make_rig(tmp_path, Provider(submit_error=error))
    try:
        failed = asyncio.run(rig.run())
        assert failed.status is JobStatus.FAILED
        assert failed.error.code == "SUBMIT_UNCERTAIN" and failed.error.retryable is None
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
        assert CANARY not in failed.model_dump_json() + rig.logs.getvalue()
    finally:
        manager.close()


@pytest.mark.parametrize("commit", [False, True])
def test_remote_acknowledgement_save_failure_never_resubmits(tmp_path, commit):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=True))
    rig.repo.fail = lambda job: job.status is JobStatus.SUBMITTED
    rig.repo.commit = commit
    try:
        if commit:
            completed = asyncio.run(rig.run())
            assert completed.status is JobStatus.COMPLETED
        else:
            with pytest.raises(ImageHistoryError) as caught:
                asyncio.run(rig.run())
            assert caught.value.job.remote_ref == rig.provider.remote_ref()
            assert "fake_job_1" in str(caught.value) and "media" in str(caught.value)
            assert CANARY not in "".join(traceback.format_exception(caught.value))
            assert rig.clock.waits == [] and rig.provider.fetch_count == 0
            diagnostic = json.loads(rig.logs.getvalue().splitlines()[-1])
            assert diagnostic["event"] == "remote_ref_persistence_failed"
            assert diagnostic["remote_job_id"] == "fake_job_1"
        assert rig.provider.submit_count == 1
        assert sum(j.status is JobStatus.SUBMITTED for j in rig.repo.saves) == 1
    finally:
        manager.close()


@pytest.mark.parametrize("kind", ["download", "conversion", "missing", "invalid", "second"])
def test_local_failure_retains_cost_ref_and_partial_artifacts(tmp_path, kind):
    rig, manager = make_rig(
        tmp_path, Provider(asynchronous=True, count=2 if kind == "second" else 1)
    )
    if kind == "missing":
        rig.provider.result = rig.provider.result.model_copy(update={"remote_artifacts": []})
    elif kind == "invalid":
        rig.provider.result = rig.provider.result.model_copy(
            update={"remote_artifacts": [RemoteArtifact(kind=ArtifactKind.IMAGE)]}
        )
    else:

        async def broken(locator):
            if kind == "conversion":
                return b"not an image"
            if kind == "second" and not rig.downloads:
                return await rig.download(locator)
            raise RuntimeError(CANARY)

        rig.downloader = broken
    try:
        failed = asyncio.run(rig.run())
        assert failed.status is JobStatus.FAILED
        assert failed.cost.amount == Decimal("4.800")
        assert failed.remote_ref == rig.provider.remote_ref()
        assert failed.usage.raw["images"] == rig.provider.result.usage.raw["images"]
        if kind == "second":
            assert len(failed.result.artifacts) == 1
            assert rig.artifacts.exists(failed.result.artifacts[0])
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
        assert CANARY not in failed.model_dump_json() + rig.logs.getvalue()
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        assert PeeweeJobRepository(reopened).get(failed.id) == failed


@pytest.mark.parametrize("commit", [False, True])
def test_final_history_failure_preserves_published_file_and_billing(tmp_path, commit):
    rig, manager = make_rig(tmp_path)
    rig.repo.fail = lambda job: job.status is JobStatus.COMPLETED
    rig.repo.commit = commit
    try:
        if commit:
            job = asyncio.run(rig.run())
            assert job.status is JobStatus.COMPLETED
        else:
            with pytest.raises(ImageHistoryError) as caught:
                asyncio.run(rig.run())
            job = caught.value.job
            assert len(job.result.artifacts) == 1
            persisted = rig.repo.get(job.id)
            assert persisted.status is JobStatus.SUBMITTED
            assert persisted.cost == job.cost
            assert CANARY not in "".join(traceback.format_exception(caught.value))
        assert job.remote_ref == rig.provider.remote_ref()
        assert job.cost.amount == Decimal("4.800")
        assert rig.artifacts.exists(job.result.artifacts[0])
        assert rig.provider.submit_count == 1
    finally:
        manager.close()


@pytest.mark.parametrize("commit", [False, True])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_final_commit_interrupt_preserves_published_snapshot_and_original_interrupt(
    tmp_path, commit, interrupt_type
):
    rig, manager = make_rig(tmp_path, refs=1)
    interrupt = interrupt_type()
    save = rig.repo.save
    completed_attempts = []

    def interrupted_save(job):
        if job.status is JobStatus.COMPLETED:
            completed_attempts.append(job)
            if commit:
                save(job)  # Real Peewee transaction commits before losing acknowledgement.
            raise interrupt
        return save(job)

    rig.repo.save = interrupted_save
    try:
        with pytest.raises(interrupt_type) as caught:
            asyncio.run(rig.run())
        assert caught.value is interrupt
        assert len(completed_attempts) == 1
        attempted = completed_attempts[0]
        persisted = rig.repo.get(attempted.id)
        if commit:
            assert persisted == attempted and persisted.status is JobStatus.COMPLETED
        else:
            assert persisted.status is JobStatus.SUBMITTED
            assert persisted.result.artifacts == ()
        snapshot = caught.value.__cause__.job
        assert snapshot.result.artifacts == attempted.result.artifacts
        assert snapshot.remote_ref == attempted.remote_ref == rig.provider.remote_ref()
        assert snapshot.cost == attempted.cost == rig.provider.result.cost
        assert snapshot.inputs == attempted.inputs
        assert rig.artifacts.exists(snapshot.result.artifacts[0])
        if not commit:
            assert snapshot.status is JobStatus.SUBMITTED
        assert not any(job.status is JobStatus.FAILED for job in rig.repo.saves)
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        assert PeeweeJobRepository(reopened).get(attempted.id) == persisted
        content = rig.artifacts.resolve_path(snapshot.result.artifacts[0]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == snapshot.result.artifacts[0].sha256


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_interrupt_confirming_final_save_never_overwrites_completed(tmp_path, interrupt_type):
    rig, manager = make_rig(tmp_path)
    interrupt = interrupt_type()
    read = rig.repo.get
    interrupted = False

    def interrupted_read(job_id):
        nonlocal interrupted
        stored = read(job_id)
        if stored.status is JobStatus.COMPLETED and not interrupted:
            interrupted = True
            raise interrupt
        return stored

    rig.repo.get = interrupted_read
    try:
        with pytest.raises(interrupt_type) as caught:
            asyncio.run(rig.run())
        assert caught.value is interrupt
        persisted = read(1)
        assert persisted.status is JobStatus.COMPLETED
        assert persisted.result.artifacts and rig.artifacts.exists(persisted.result.artifacts[0])
        assert not any(job.status is JobStatus.FAILED for job in rig.repo.saves)
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        assert PeeweeJobRepository(reopened).get(persisted.id) == persisted


@pytest.mark.parametrize("kind", ["missing", "file", "symlink", "junction", "unwritable"])
def test_output_preflight_failure_is_persisted_before_paid_submit(tmp_path, monkeypatch, kind):
    rig, manager = make_rig(tmp_path, refs=1)
    output = tmp_path / "selected-output"
    if kind == "file":
        output.write_bytes(b"foreign-file")
    elif kind not in ("missing", "file"):
        output.mkdir()
        (output / "foreign.png").write_bytes(b"foreign-file")
    if kind in ("symlink", "junction"):
        attribute = "is_symlink" if kind == "symlink" else "is_junction"
        original = getattr(Path, attribute)
        monkeypatch.setattr(Path, attribute, lambda path: path == output or original(path))
    elif kind == "unwritable":
        # Deterministic access denial at the real filesystem adapter's write probe,
        # independent of Windows ACLs or whether the test process is privileged.
        import tempfile

        original = tempfile.mkstemp

        def denied(*args, **kwargs):
            if Path(kwargs.get("dir", "")) == output:
                raise PermissionError(CANARY)
            return original(*args, **kwargs)

        monkeypatch.setattr(tempfile, "mkstemp", denied)
    try:
        failed = asyncio.run(rig.run(output_dir=output))
        assert failed.status is JobStatus.FAILED
        assert rig.repo.get(failed.id) == failed
        assert failed.cost is None and failed.remote_ref is None
        assert rig.provider.submit_count == 0 and rig.provider.cancel_count == 0
        assert rig.downloads == []
        assert not (rig.root / "inputs").exists()
        assert CANARY not in failed.model_dump_json() + rig.logs.getvalue()
        if kind == "missing":
            assert not output.exists()
        elif kind == "file":
            assert output.read_bytes() == b"foreign-file"
        else:
            assert (output / "foreign.png").read_bytes() == b"foreign-file"
            assert list(output.iterdir()) == [output / "foreign.png"]
    finally:
        manager.close()


@pytest.mark.parametrize("external", [False, True])
def test_output_preflight_is_ready_at_submit_without_clobber(tmp_path, external):
    rig, manager = make_rig(tmp_path)
    output = tmp_path / "user-output" if external else rig.root / "outputs" / "1"
    if external:
        output.mkdir()
        (output / "result.png").write_bytes(b"foreign-file")
    submit = rig.provider.submit

    async def checked_submit(request):
        assert output.is_dir()
        assert list(output.glob(".aimedia-*.part")) == []
        assert rig.repo.list_recent()[0].status is JobStatus.CREATED
        return await submit(request)

    rig.provider.submit = checked_submit
    try:
        completed = asyncio.run(rig.run(output_dir=output if external else None))
        assert completed.status is JobStatus.COMPLETED
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
        artifact = completed.result.artifacts[0]
        assert artifact.metadata["ownership"] == ("user_output" if external else "managed")
        if external:
            assert (output / "result.png").read_bytes() == b"foreign-file"
            assert artifact.local_path == output / "result_002.png"
            assert not (rig.root / "outputs").exists()
    finally:
        manager.close()


@pytest.mark.parametrize("interrupt", [None, asyncio.CancelledError(), KeyboardInterrupt()])
def test_local_wait_stops_without_remote_cancel(tmp_path, interrupt):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=True, pending_polls=100))
    rig.clock.interrupt = interrupt
    try:
        if interrupt is None:
            failed = asyncio.run(rig.run(wait_timeout=2.5))
            assert failed.error.code == "JOB_TIMEOUT"
            assert rig.clock.waits == [1.0, 1.0, 0.5]
        else:
            with pytest.raises(type(interrupt)):
                asyncio.run(rig.run())
            failed = rig.repo.list_recent()[0]
            assert failed.error.code == "JOB_WAIT_INTERRUPTED"
        assert failed.status is JobStatus.FAILED
        assert failed.remote_ref == rig.provider.remote_ref()
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()


@pytest.mark.parametrize("interrupt", [asyncio.CancelledError(), KeyboardInterrupt()])
def test_interrupt_during_submit_is_unknown_not_remote_cancel(tmp_path, interrupt):
    rig, manager = make_rig(tmp_path)

    async def submit(request):
        rig.provider.submit_count += 1
        raise interrupt

    rig.provider.submit = submit
    try:
        with pytest.raises(type(interrupt)):
            asyncio.run(rig.run())
        failed = rig.repo.list_recent()[0]
        assert failed.error.code == "SUBMIT_UNCERTAIN" and failed.error.retryable is None
        assert failed.remote_ref is None
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
    finally:
        manager.close()


@pytest.mark.parametrize("cost", [None, "0.000"])
def test_unknown_price_is_not_zero(tmp_path, cost):
    rig, manager = make_rig(tmp_path, Provider(cost=cost))
    try:
        job = asyncio.run(rig.run())
        assert job.has_known_cost is (cost is not None)
        if cost is not None:
            assert job.cost.is_zero
    finally:
        manager.close()


def test_multiple_required_images_complete_only_after_all_files(tmp_path):
    rig, manager = make_rig(tmp_path, Provider(count=2))
    try:
        completed = asyncio.run(rig.run())
        assert len(completed.result.artifacts) == len(rig.downloads) == 2
        assert all(rig.artifacts.exists(a) for a in completed.result.artifacts)
        assert len({a.local_path for a in completed.result.artifacts}) == 2
    finally:
        manager.close()


def test_arbitrary_result_fields_never_reach_history_or_logs(tmp_path):
    rig, manager = make_rig(tmp_path)
    rig.provider.result = rig.provider.result.model_copy(
        update={
            "provider_metadata": {
                "model": CANARY,
                "body": CANARY,
                "base64": CANARY,
                "api_key": CANARY,
                "url": f"https://s3.polza.ai/x?sig={CANARY}",
            },
            "usage": Usage(
                output_units=1,
                raw={"images": 1, "body": CANARY, "Authorization": CANARY, "nested": {"x": CANARY}},
            ),
            "content": CANARY,
        }
    )
    try:
        completed = asyncio.run(rig.run())
        assert completed.usage.raw == {"images": 1}
        assert completed.result.metadata == {} and completed.result.content is None
        assert CANARY not in completed.model_dump_json() + rig.logs.getvalue()
        assert rig.downloads[0].url.endswith(CANARY)
    finally:
        manager.close()
    assert CANARY.encode() not in (rig.root / "database.sqlite3").read_bytes()


def test_real_polza_gateway_composed_after_created_uses_managed_transport(tmp_path):
    rig, manager = make_rig(tmp_path, Provider(provider_id="polza"), refs=1)
    posts = []
    record = ModelRecord(
        schema_version=1,
        model_id="synthetic-image",
        name="Synthetic",
        family="image",
        status="active",
        capabilities={"reference_images": CapabilityNode(supported=True, max=4)},
        inputs={"images": InputLimit(supported=True, max=4, formats=("png",))},
        outputs={},
        parameters={},
        providers={"polza": ProviderBinding(remote_model_id="synthetic/image")},
    )

    async def flow():
        async def handler(request):
            posts.append(request)
            payload = json.loads(request.content)
            assert payload["model"] == "synthetic/image"
            return httpx.Response(
                200,
                json={
                    "id": "aig_single",
                    "object": "media.generation",
                    "status": "completed",
                    "data": [{"url": f"https://s3.polza.ai/x.png?sig={CANARY}"}],
                    "usage": {"cost_rub": "4.800"},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            def prepare(request):
                rig.prepare(request)
                effective = ModelResolver([record]).resolve(request.model.id, request.provider.id)
                validate_model_request(effective, request)
                return ImageExecutionSetup(
                    binding=ProviderModelBinding(
                        provider_id="polza", remote_model_id="synthetic/image"
                    ),
                    gateway=PolzaProviderGateway(
                        client=client,
                        api_key=CANARY,
                        effective=effective,
                        max_body_bytes=100000,
                        max_response_bytes=100000,
                    ),
                )

            rig.setup = prepare
            return await rig.run()

    rig.snapshots[0].ref.path.unlink()
    try:
        completed = asyncio.run(flow())
        assert completed.status is JobStatus.COMPLETED and len(posts) == 1
        assert completed.remote_ref.remote_job_id == "aig_single"
        assert completed.inputs[0].managed_path is not None
        assert CANARY not in completed.model_dump_json() + rig.logs.getvalue()
    finally:
        manager.close()


@pytest.mark.parametrize(
    "model,refs",
    [("qwen-image-2-1", 0), ("gemini-3-1-flash-image-preview", 1), ("gpt-5-4-image-2-mie", 0)],
)
@pytest.mark.parametrize("ack_status", ["pending", "processing", "completed"])
def test_documented_async_ack_is_durable_before_get_and_finalizes(
    tmp_path, model, refs, ack_status
):
    rig, manager = make_rig(tmp_path, Provider(provider_id="polza", cost="3.000"), refs=refs)
    rig.request = rig.request.model_copy(
        update={"model": ModelRef(id=model), "resolution": "1K", "aspect_ratio": "16:9"}
    )
    effective = ModelResolver(load_builtin_registry()).resolve(model, "polza")
    ref = RemoteJobRef(
        provider_id="polza", remote_job_id="opaque_async_007", operation=RemoteOperation.MEDIA
    )
    calls = []
    completed_body = {
        "id": ref.remote_job_id,
        "object": "media.generation",
        "status": "completed",
        "data": [{"url": f"https://s3.polza.ai/x.png?sig={CANARY}"}],
        "usage": {"cost_rub": "3.000", "output_units": 1},
    }

    async def flow():
        def handler(sent):
            calls.append(sent.method)
            if sent.method == "POST":
                body = json.loads(sent.content)
                assert body["async"] is True and "async" not in body["input"]
                assert body["model"] == effective.remote_model_id
                assert len(body["input"].get("images", [])) == refs
                return httpx.Response(
                    200,
                    json=completed_body
                    if ack_status == "completed"
                    else {
                        "id": ref.remote_job_id,
                        "object": "media.generation",
                        "status": ack_status,
                    },
                )
            stored = rig.repo.real.get(rig.repo.list_recent()[0].id)
            assert stored.remote_ref == ref and stored.submitted_at == NOW
            assert stored.status in (JobStatus.SUBMITTED, JobStatus.RUNNING)
            assert sent.url.path == f"/api/v1/media/{ref.remote_job_id}"
            return httpx.Response(200, json=completed_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            def prepare(request):
                rig.prepare(request)
                validate_model_request(effective, request)
                return ImageExecutionSetup(
                    binding=ProviderModelBinding(
                        provider_id="polza", remote_model_id=effective.remote_model_id
                    ),
                    gateway=PolzaProviderGateway(
                        client=client,
                        api_key=CANARY,
                        effective=effective,
                        max_body_bytes=100000,
                        max_response_bytes=100000,
                    ),
                )

            rig.setup = prepare
            return await rig.run()

    for snapshot in rig.snapshots:
        snapshot.ref.path.unlink()
    try:
        completed = asyncio.run(flow())
        assert completed.status is JobStatus.COMPLETED
        assert completed.remote_ref == ref
        assert completed.cost.amount == Decimal("3.000")
        assert completed.usage.output_units == 1
        assert calls == (["POST"] if ack_status == "completed" else ["POST", "GET", "GET"])
        artifact = completed.result.artifacts[0]
        assert rig.artifacts.resolve_path(artifact).read_bytes() == png_bytes()
        assert artifact.sha256 == hashlib.sha256(png_bytes()).hexdigest()
        assert CANARY not in completed.model_dump_json() + rig.logs.getvalue()
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        assert PeeweeJobRepository(reopened).get(completed.id) == completed


@pytest.mark.parametrize(
    "body",
    [
        {"taskId": "opaque_async_007"},
        {"taskId": "opaque_async_007", "object": "media.generation", "status": "pending"},
        {
            "id": "opaque_async_007",
            "object": "media.generation",
            "status": "completed",
            "data": [],
            "usage": {"cost_rub": "3.000"},
        },
        b"not-json",
    ],
    ids=["taskId-only", "taskId-with-status", "zero-images-with-cost", "malformed-json"],
)
def test_documented_async_unknown_submit_has_no_invented_ref_billing_or_artifacts(tmp_path, body):
    rig, manager = make_rig(tmp_path, Provider(provider_id="polza"))
    model = "qwen-image-2-1"
    rig.request = rig.request.model_copy(update={"model": ModelRef(id=model)})
    effective = ModelResolver(load_builtin_registry()).resolve(model, "polza")
    calls = []

    async def flow():
        def handler(sent):
            calls.append(sent.method)
            assert sent.method == "POST" and json.loads(sent.content)["async"] is True
            return (
                httpx.Response(200, content=body)
                if isinstance(body, bytes)
                else httpx.Response(200, json=body)
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            def prepare(request):
                rig.prepare(request)
                return ImageExecutionSetup(
                    binding=ProviderModelBinding(
                        provider_id="polza", remote_model_id=effective.remote_model_id
                    ),
                    gateway=PolzaProviderGateway(
                        client=client,
                        api_key=CANARY,
                        effective=effective,
                        max_body_bytes=100000,
                        max_response_bytes=100000,
                    ),
                )

            rig.setup = prepare
            return await rig.run()

    try:
        failed = asyncio.run(flow())
        assert failed.status is JobStatus.FAILED and failed.error.code == "SUBMIT_UNCERTAIN"
        assert failed.error.retryable is None
        assert failed.remote_ref is None and failed.cost is None and failed.usage is None
        assert failed.artifacts == () and rig.downloads == []
        assert calls == ["POST"]
        assert rig.repo.get(failed.id) == failed
    finally:
        manager.close()


@pytest.mark.parametrize("ack_status", ["pending", "processing"])
def test_documented_async_known_ref_timeout_restarts_with_get_only_sync(tmp_path, ack_status):
    rig, manager = make_rig(tmp_path, Provider(provider_id="polza", cost="3.000"), refs=1)
    model = "gemini-3-1-flash-image-preview"
    rig.request = rig.request.model_copy(update={"model": ModelRef(id=model)})
    effective = ModelResolver(load_builtin_registry()).resolve(model, "polza")
    ref = RemoteJobRef(
        provider_id="polza", remote_job_id="opaque_async_007", operation=RemoteOperation.MEDIA
    )
    calls = []

    async def initial():
        def handler(sent):
            calls.append(sent.method)
            if sent.method == "POST":
                assert json.loads(sent.content)["async"] is True
            else:
                assert rig.repo.list_recent()[0].remote_ref == ref
                assert sent.url.path == f"/api/v1/media/{ref.remote_job_id}"
            return httpx.Response(
                200,
                json={
                    "id": ref.remote_job_id,
                    "object": "media.generation",
                    "status": ack_status if sent.method == "POST" else "processing",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            def prepare(request):
                rig.prepare(request)
                return ImageExecutionSetup(
                    binding=ProviderModelBinding(
                        provider_id="polza", remote_model_id=effective.remote_model_id
                    ),
                    gateway=PolzaProviderGateway(
                        client=client,
                        api_key=CANARY,
                        effective=effective,
                        max_body_bytes=100000,
                        max_response_bytes=100000,
                    ),
                )

            rig.setup = prepare
            return await rig.run(wait_timeout=1.5)

    try:
        failed = asyncio.run(initial())
        assert failed.status is JobStatus.FAILED and failed.error.code == "JOB_TIMEOUT"
        assert failed.remote_ref == ref and failed.cost is None
        assert calls == ["POST", "GET"]
    finally:
        manager.close()
    with open_database(rig.root / "database.sqlite3") as reopened:
        rig.repo = Repository(PeeweeJobRepository(reopened))
        assert rig.repo.get(failed.id) == failed

        async def restart():
            def handler(sent):
                calls.append(sent.method)
                assert sent.method == "GET"
                assert sent.url.path == f"/api/v1/media/{ref.remote_job_id}"
                return httpx.Response(
                    200,
                    json={
                        "id": ref.remote_job_id,
                        "object": "media.generation",
                        "status": "completed",
                        "data": [{"url": f"https://s3.polza.ai/x.png?sig={CANARY}"}],
                        "usage": {"cost_rub": "3.000", "output_units": 1},
                    },
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                gateway = PolzaProviderGateway(
                    client=client,
                    api_key=CANARY,
                    effective=effective,
                    max_body_bytes=100000,
                    max_response_bytes=100000,
                )
                services = ImageServices(
                    repository=rig.repo,
                    inputs=rig.inputs,
                    artifacts=rig.artifacts,
                    prepare=rig.setup,
                    sync_gateway=lambda job: gateway,
                    download=rig.download,
                    claim=lambda job_id: claim_job(rig.root, job_id),
                )
                return await sync_image(failed.id, services, gateway)

        recovered = asyncio.run(restart())
        assert recovered.error is None and recovered.job.status is JobStatus.COMPLETED
        assert recovered.job.remote_ref == ref
        assert recovered.job.cost.amount == Decimal("3.000")
        assert recovered.job.recovery.previous_error == failed.error
        assert len(recovered.job.result.artifacts) == 1
        assert calls == ["POST", "GET", "GET", "GET"]


@pytest.mark.parametrize("stage", ["billing", "partial"])
@pytest.mark.parametrize("commit", [False, True])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_nonfinal_commit_interrupt_retains_known_snapshot_without_stale_failure_write(
    tmp_path, stage, commit, interrupt_type
):
    rig, manager = make_rig(tmp_path, Provider(asynchronous=True, count=2))
    save = rig.repo.save
    interrupt = interrupt_type()
    attempts = []

    def interrupted_save(job):
        matches = job.cost is not None and job.status is not JobStatus.COMPLETED
        if stage == "partial":
            matches = matches and job.result is not None and len(job.result.artifacts) == 1
        if matches and not attempts:
            attempts.append(job)
            if commit:
                save(job)
            raise interrupt
        return save(job)

    rig.repo.save = interrupted_save
    try:
        with pytest.raises(interrupt_type) as caught:
            asyncio.run(rig.run())
        assert caught.value is interrupt
        snapshot = caught.value.__cause__.job
        assert snapshot == attempts[0]
        assert snapshot.remote_ref == rig.provider.remote_ref()
        assert snapshot.cost.amount == Decimal("4.800")
        persisted = rig.repo.get(snapshot.id)
        if commit:
            assert persisted == snapshot
        assert not any(job.status is JobStatus.FAILED for job in rig.repo.saves)
        assert rig.provider.submit_count == 1 and rig.provider.cancel_count == 0
        if stage == "partial":
            assert rig.artifacts.exists(snapshot.result.artifacts[0])
    finally:
        manager.close()


@pytest.mark.parametrize(
    "status", [100, 400, 599, True, False, 99, 600, "400", 400.0, None, {}, []]
)
def test_safe_error_preserves_only_strict_http_status(status):
    from aimedia.application.single_image import _safe_error

    safe = _safe_error(
        JobError(
            code="SUBMIT_UNCERTAIN",
            message=CANARY,
            provider_message=CANARY,
            provider_code="api_key_revoked",
            retryable=True,
            details={
                "http_status": status,
                "reason": "noProvidersForModel",
                "trace_id": CANARY,
                "raw": CANARY,
                "headers": {"Authorization": CANARY},
                "url": CANARY,
            },
        ),
        "Provider submit failed",
    )
    assert safe.details == {
        "reason": "noProvidersForModel",
        **({"http_status": status} if type(status) is int and 100 <= status <= 599 else {}),
    }
    assert safe.provider_code == "api_key_revoked" and safe.retryable is None
    assert CANARY not in safe.model_dump_json()


@pytest.mark.parametrize("stage", ["preparation", "submit", "result", "finalization"])
def test_safe_http_diagnostics_persist_failed_job_and_reopen(tmp_path, stage):
    class Rejected(Provider):
        def reject(self):
            raise ProviderError(
                JobError(
                    code="PROVIDER_HTTP_ERROR",
                    message=CANARY,
                    provider_message=CANARY,
                    provider_code="BAD_REQUEST",
                    retryable=False,
                    details={
                        "http_status": 400,
                        "reason": "noProvidersForModel",
                        "trace_id": CANARY,
                        "body": CANARY,
                    },
                )
            )

        async def submit(self, request):
            if stage == "submit":
                self.submit_count += 1
                self.reject()
            return await super().submit(request)

        async def fetch_result(self, ref):
            if stage == "result":
                self.reject()
            return await super().fetch_result(ref)

    rig, manager = make_rig(tmp_path, Rejected(asynchronous=stage == "result"))
    if stage == "preparation":
        rig.setup = lambda _: rig.provider.reject()
    if stage == "finalization":

        async def failed_download(_):
            rig.provider.reject()

        rig.downloader = failed_download
    try:
        failed = asyncio.run(rig.run())
        assert failed.status is JobStatus.FAILED
        assert failed.error.provider_code == "BAD_REQUEST"
        assert failed.error.details == {"http_status": 400, "reason": "noProvidersForModel"}
        assert failed.error.retryable is False
        assert rig.provider.submit_count == (0 if stage == "preparation" else 1)
        assert CANARY not in failed.model_dump_json() + rig.logs.getvalue()
    finally:
        manager.close()
    reopened = open_database(rig.root / "database.sqlite3")
    try:
        assert PeeweeJobRepository(reopened).get(failed.id).error == failed.error
    finally:
        reopened.close()
