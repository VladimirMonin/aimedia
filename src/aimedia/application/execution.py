"""Batch, explicit retry and same-execution sync over domain ports only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aimedia.application.inputs.prepare import ReferenceSnapshot, validate_reference_content
from aimedia.application.single_image import (
    ImageAttemptFailed,
    ImageExecutionSetup,
    ImageHistoryError,
    _fail,
    _safe_error,
    _save_confirmed,
    _updated,
    finish_image_result,
    generate_image,
    safe_get,
)
from aimedia.domain.artifacts import RemoteArtifact
from aimedia.domain.errors import InvalidParameterValueError, JobError, ProviderError
from aimedia.domain.inputs import InputRef, PromptSource
from aimedia.domain.job import Job, JobRelation, JobResult
from aimedia.domain.ports import (
    ArtifactStorage,
    JobRepository,
    ManagedInputStorage,
    PollingProviderGateway,
)
from aimedia.domain.refs import ProviderJobState
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.domain.state import JobStatus

RECOVERABLE_ERRORS = frozenset(
    {
        "JOB_TIMEOUT",
        "JOB_WAIT_INTERRUPTED",
        "IMAGE_FINALIZATION_FAILED",
        "PROVIDER_INCOMPLETE_RESULT",
        "ARTIFACT_DOWNLOAD_FAILED",
        "OUTPUT_WRITE_FAILED",
        "LOCAL_CONVERSION_FAILED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_TRANSPORT_ERROR",
    }
)


def can_sync(job: Job) -> bool:
    return job.remote_ref is not None and (
        job.status in {JobStatus.SUBMITTED, JobStatus.RUNNING}
        or (
            job.status is JobStatus.FAILED
            and job.error is not None
            and job.error.code in RECOVERABLE_ERRORS
        )
    )


@dataclass(frozen=True)
class PreparedImage:
    request: ImageGenerationRequest
    snapshots: tuple[ReferenceSnapshot, ...] = ()
    sources: tuple[PromptSource, ...] = ()


@dataclass(frozen=True)
class ImageServices:
    repository: JobRepository
    inputs: ManagedInputStorage
    artifacts: ArtifactStorage
    prepare: Callable[[ImageGenerationRequest], ImageExecutionSetup]
    sync_gateway: Callable[[Job], PollingProviderGateway]
    download: Callable[[RemoteArtifact], Awaitable[bytes]]
    claim: Callable[[int], AbstractContextManager[None]]


def create_image_job(
    prepared: PreparedImage,
    services: ImageServices,
    *,
    output_dir: Path | None = None,
    base_name: str | None = None,
    keep_original: bool = False,
    relation: JobRelation | None = None,
) -> Job:
    request = prepared.request
    return _save_confirmed(
        services.repository,
        Job(
            kind=request.kind,
            provider=request.provider,
            model=request.model,
            request=request,
            inputs=request.images,
            compiled_prompt=request.prompt,
            prompt_sources=list(prepared.sources),
            relation=relation,
            created_at=datetime.now(UTC),
            result=JobResult(
                metadata={
                    "output": {
                        "directory": str(output_dir) if output_dir else None,
                        "base_name": base_name,
                        "keep_original": keep_original,
                    }
                }
            ),
        ),
    )


async def execute_image(
    prepared: PreparedImage,
    services: ImageServices,
    *,
    created_job: Job | None = None,
    output_dir: Path | None = None,
    base_name: str | None = None,
    keep_original: bool = False,
    relation: JobRelation | None = None,
    poll_interval: float = 1.0,
    wait_timeout: float = 300.0,
) -> Job:
    job = created_job or create_image_job(
        prepared,
        services,
        output_dir=output_dir,
        base_name=base_name,
        keep_original=keep_original,
        relation=relation,
    )
    assert job.id is not None
    with services.claim(job.id):
        # Re-read after acquiring kernel ownership, before any paid work.
        if services.repository.get(job.id) != job or job.status is not JobStatus.CREATED:
            raise ImageHistoryError(job)
        return await generate_image(
            prepared.request,
            prepared.snapshots,
            repository=services.repository,
            input_storage=services.inputs,
            artifact_storage=services.artifacts,
            prepare_provider=services.prepare,
            download=services.download,
            prompt_sources=prepared.sources,
            created_job=job,
            output_dir=output_dir,
            base_name=base_name,
            keep_original=keep_original,
            relation=relation,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
        )


async def execute_batch(
    prepared: Sequence[PreparedImage],
    services: ImageServices,
    *,
    concurrency: int = 3,
    output_dir: Path | None = None,
    base_name: str | None = None,
    keep_original: bool = False,
    poll_interval: float = 1.0,
    wait_timeout: float = 300.0,
) -> tuple[Job, ...]:
    if isinstance(concurrency, bool) or not 1 <= concurrency <= 32:
        raise InvalidParameterValueError("Concurrency должна быть от 1 до 32")
    jobs = [
        create_image_job(
            item, services, output_dir=output_dir, base_name=base_name, keep_original=keep_original
        )
        for item in prepared
    ]
    semaphore = asyncio.Semaphore(concurrency)

    async def run(item: PreparedImage, job: Job) -> Job:
        try:
            async with semaphore:
                return await execute_image(
                    item,
                    services,
                    created_job=job,
                    output_dir=output_dir,
                    base_name=base_name,
                    keep_original=keep_original,
                    poll_interval=poll_interval,
                    wait_timeout=wait_timeout,
                )
        except asyncio.CancelledError:
            assert job.id is not None
            persisted = services.repository.get(job.id)
            if persisted and persisted.status is JobStatus.CREATED:
                _save_confirmed(
                    services.repository,
                    _updated(persisted, status=JobStatus.CANCELLED, completed_at=datetime.now(UTC)),
                )
            raise

    tasks = [asyncio.create_task(run(item, job)) for item, job in zip(prepared, jobs, strict=True)]
    try:
        return tuple(await asyncio.gather(*tasks))
    finally:
        # Unexpected infrastructure failure stops unpaid neighbors; expected Job
        # failures are returned above, so they never cancel successful neighbors.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def prepare_retry(job: Job, services: ImageServices) -> PreparedImage:
    if job.id is None:
        raise InvalidParameterValueError("Retry требует сохранённый Job")
    snapshots = []
    refs = []
    for ref in job.inputs:
        # Managed data is authoritative, never reread a deleted/replaced source.
        path = services.inputs.resolve_path(ref)
        assert ref.size_bytes is not None
        with path.open("rb") as stream:
            content = stream.read(ref.size_bytes + 1)
        validate_reference_content(ref, content)
        fresh = InputRef.model_validate({**ref.model_dump(), "managed_path": None})
        refs.append(fresh)
        snapshots.append(ReferenceSnapshot(ref=fresh, content=content))
    request = ImageGenerationRequest.model_validate({**job.request.model_dump(), "images": refs})
    return PreparedImage(request, tuple(snapshots), tuple(job.prompt_sources))


@dataclass(frozen=True)
class SyncResult:
    """Outcome of this observation, not the persisted Job's previous failure."""

    job: Job
    error: JobError | None = None


async def sync_image(
    job_id: int,
    services: ImageServices,
    gateway: PollingProviderGateway,
) -> SyncResult:
    """No submit method is called anywhere in the sync path."""
    with services.claim(job_id):
        job = services.repository.get(job_id)
        if job is None:
            raise InvalidParameterValueError("Job не найден")
        if job.status is JobStatus.COMPLETED:
            return SyncResult(job)
        if job.remote_ref is None or job.status is JobStatus.CANCELLED:
            raise InvalidParameterValueError("Нет известного remote execution для sync")
        if not can_sync(job):
            raise InvalidParameterValueError("Этот failure не восстанавливается в том же Job")
        try:
            state = await safe_get(lambda: gateway.get_status(job.remote_ref))  # type: ignore[arg-type]
            if state is ProviderJobState.COMPLETED:
                result = await safe_get(lambda: gateway.fetch_result(job.remote_ref))  # type: ignore[arg-type]
                output = job.result.metadata.get("output", {}) if job.result else {}
                options = output if isinstance(output, dict) else {}
                finished = await finish_image_result(
                    job,
                    result,
                    repository=services.repository,
                    artifact_storage=services.artifacts,
                    download=services.download,
                    output_dir=Path(options["directory"]) if options.get("directory") else None,
                    base_name=options.get("base_name"),
                    keep_original=bool(options.get("keep_original")),
                    recovery=job.status is JobStatus.FAILED,
                    raise_on_failure=True,
                )
                return SyncResult(finished)
            if state is ProviderJobState.RUNNING and job.status is JobStatus.SUBMITTED:
                return SyncResult(
                    _save_confirmed(
                        services.repository,
                        _updated(job, status=JobStatus.RUNNING, started_at=datetime.now(UTC)),
                    )
                )
            if state in (ProviderJobState.FAILED, ProviderJobState.CANCELLED):
                error = JobError(code="REMOTE_GENERATION_FAILED", message="Remote image failed")
                return SyncResult(_fail(services.repository, job, error, datetime.now(UTC)), error)
            # FAILED remains terminal during a running observation; original error retained.
            return SyncResult(job)
        except ImageAttemptFailed as exc:
            return SyncResult(exc.job, exc.error)
        except ProviderError as exc:
            error = _safe_error(exc.error, "Provider sync failed")
            return SyncResult(_fail(services.repository, job, error, datetime.now(UTC)), error)
