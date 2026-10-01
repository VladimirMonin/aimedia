"""One new image Job; no retry, recovery, batch or infrastructure composition."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from aimedia.application.artifact_finalization import (
    ArtifactHistoryWriteError,
    finalize_image_artifact,
)
from aimedia.application.inputs.archive import archive_reference_images
from aimedia.application.inputs.prepare import ReferenceSnapshot
from aimedia.domain.artifacts import Artifact, ArtifactKind, ArtifactRole, RemoteArtifact
from aimedia.domain.costs import Usage
from aimedia.domain.errors import DomainError, JobError, ProviderError, UnknownProviderError
from aimedia.domain.inputs import PromptSource
from aimedia.domain.job import Job, JobRelation, JobResult
from aimedia.domain.ports import (
    ArtifactStorage,
    JobRepository,
    ManagedInputStorage,
    PollingProviderGateway,
    ProviderGateway,
    ProviderResult,
)
from aimedia.domain.refs import ProviderJobState, ProviderModelBinding
from aimedia.domain.requests import ImageGenerationRequest
from aimedia.domain.state import JobStatus, ensure_transition
from aimedia.logging import EventLogger, redact

# Remote model identity only: a bounded ASCII ID with an optional route qualifier,
# never a URL. Logical IDs and opaque remote job IDs keep their own boundaries.
_SAFE_REMOTE_MODEL_ID = re.compile(
    r"(?=.{1,128}\Z)[A-Za-z0-9][A-Za-z0-9_./-]*(?:@[a-z0-9]+(?:-[a-z0-9]+)*)?\Z"
)


@dataclass(frozen=True)
class ImageExecutionSetup:
    """Outer callback validates/resolves binding and creates the injected gateway."""

    binding: ProviderModelBinding
    gateway: ProviderGateway


class ImageHistoryError(Exception):
    """History not confirmed: stop; snapshot retains known ref/cost/partial files.

    The diagnostic message contains only checked identifiers, never provider data.
    The snapshot is local forensic state, not a claim of a committed transaction.
    """

    def __init__(self, job: Job) -> None:
        ref = job.remote_ref
        super().__init__(
            f"Image history not confirmed: job_id={job.id}; "
            f"remote_job_id={ref.remote_job_id if ref else None}; "
            f"operation={ref.operation.value if ref and ref.operation else None}"
        )
        self.job = job


class ImageAttemptFailed(Exception):
    """Current finalization failure, separate from an older terminal Job error."""

    def __init__(self, job: Job, error: JobError) -> None:
        super().__init__(error.code)
        self.job = job
        self.error = error


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def generate_image(
    request: ImageGenerationRequest,
    snapshots: Sequence[ReferenceSnapshot],
    *,
    repository: JobRepository,
    input_storage: ManagedInputStorage,
    artifact_storage: ArtifactStorage,
    prepare_provider: Callable[[ImageGenerationRequest], ImageExecutionSetup],
    download: Callable[[RemoteArtifact], Awaitable[bytes]],
    prompt_sources: Sequence[PromptSource] = (),
    poll_interval: float = 1.0,
    wait_timeout: float = 300.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = _utc_now,
    output_dir: Path | None = None,
    base_name: str | None = None,
    keep_original: bool = False,
    relation: JobRelation | None = None,
    created_job: Job | None = None,
    logger: EventLogger | None = None,
) -> Job:
    """Run prepared inputs once. Source reading must finish before this call.

    Semantic validation/setup happens after confirmed CREATED (D03). URLs and
    arbitrary provider text remain in memory; history gets safe billing metrics.
    Interrupts propagate after retaining the known ref, without remote cancel.
    History uncertainty raises ImageHistoryError, never another save/submit.
    """
    for value in (poll_interval, wait_timeout):
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("Polling interval and deadline must be finite and positive")
    if request.images != [snapshot.ref for snapshot in snapshots]:
        raise ValueError("Prepared request must match reference snapshots")
    # Revalidate rather than saving an ephemeral transport request or mutable alias.
    request = ImageGenerationRequest.model_validate(request.model_dump())
    expected = Job(
        kind=request.kind,
        provider=request.provider,
        model=request.model,
        request=request,
        inputs=request.images,
        prompt_sources=list(prompt_sources),
        compiled_prompt=request.prompt,
        relation=relation,
        created_at=now(),
        result=JobResult(
            metadata={
                "output": {
                    "directory": str(output_dir) if output_dir else None,
                    "base_name": base_name,
                    "keep_original": keep_original,
                }
            }
        )
        if output_dir is not None or base_name is not None or keep_original
        else None,
    )
    if created_job is None:
        job = _save_confirmed(repository, expected)
    else:
        if (
            created_job.status is not JobStatus.CREATED
            or created_job.request != request
            or created_job.id is None
            or repository.get(created_job.id) != created_job
        ):
            raise ImageHistoryError(created_job)
        job = created_job

    def event(name: str) -> None:
        if logger is not None:
            logger.event(
                name,
                omit_correlation_fields=("command", "provider")
                + (() if job.remote_ref else ("remote_job_id", "remote_operation")),
                job_id=job.id,
                remote_job_id=job.remote_ref.remote_job_id if job.remote_ref else None,
                remote_operation=job.remote_ref.operation.value
                if job.remote_ref and job.remote_ref.operation
                else None,
            )

    event("job_created")
    setup: ImageExecutionSetup | None = None

    def validate(prepared: ImageGenerationRequest) -> None:
        nonlocal setup
        setup = prepare_provider(prepared)
        if (
            setup.binding.provider_id != prepared.provider.id
            or setup.gateway.provider_id != prepared.provider.id
            or not _SAFE_REMOTE_MODEL_ID.fullmatch(setup.binding.remote_model_id)
        ):
            raise UnknownProviderError("Provider binding does not match image request")
        assert job.id is not None
        artifact_storage.preflight(job_id=job.id, output_dir=output_dir)

    # Failures before submit are certain; no paid request occurred.
    preparation_error: JobError | None = None
    try:
        transport = archive_reference_images(
            job,
            snapshots,
            repository=repository,
            storage=input_storage,
            validate_model=validate,
        )
        archived = repository.get(job.id)  # type: ignore[arg-type]
        if archived is None:
            raise ImageHistoryError(job)
        job = archived
    except ProviderError as exc:
        preparation_error = _safe_error(exc.error, "Provider configuration failed")
    except DomainError as exc:
        preparation_error = _safe_error(exc.to_job_error(), "Image validation failed")
    except ImageHistoryError:
        raise
    except Exception:
        preparation_error = JobError(
            code="INPUT_ARCHIVE_FAILED", message="Image setup/archive failed"
        )
    if preparation_error is not None:
        # Resolve/publication may have failed after links committed. Never replace
        # these links with the initial provenance-only aggregate.
        try:
            archived = repository.get(job.id)  # type: ignore[arg-type]
        except Exception:
            archived = None
        if archived is None:
            raise ImageHistoryError(job)
        return _fail(repository, archived, preparation_error, now())
    assert setup is not None
    job = _save_confirmed(repository, _updated(job, remote_model_id=setup.binding.remote_model_id))
    event("validation_completed")

    submission_error: JobError | None = None
    interrupted: BaseException | None = None
    try:
        submission = await setup.gateway.submit(transport)
    except ProviderError as exc:
        submission_error = _safe_error(exc.error, "Provider submit failed")
    except DomainError as exc:
        submission_error = _safe_error(exc.to_job_error(), "Provider validation failed")
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        interrupted = exc
        submission_error = JobError(
            code="SUBMIT_UNCERTAIN", message="Submit outcome is unknown", retryable=None
        )
    except Exception:
        submission_error = JobError(
            code="SUBMIT_UNCERTAIN", message="Submit outcome is unknown", retryable=None
        )
    if submission_error is not None:
        job = _fail(repository, job, submission_error, now())
        if interrupted is not None:
            raise interrupted
        return job

    ref = submission.remote_ref
    # Domain RemoteJobRef permits arbitrary strings; check before diagnostics/history.
    if ref is not None and (
        ref.provider_id != job.provider.id
        or ref.operation is None
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", ref.remote_job_id)
    ):
        return _fail(
            repository,
            job,
            JobError(code="SUBMIT_UNCERTAIN", message="Submit reference is invalid"),
            now(),
        )
    status = JobStatus.SUBMITTED
    job = _updated(
        job,
        status=status,
        remote_ref=ref,
        submitted_at=now(),
        started_at=now() if status is JobStatus.RUNNING else None,
    )
    # Immediate results also carry known billing into the safe diagnostic snapshot.
    if submission.result is not None:
        job = _with_result(job, submission.result)
    try:
        job = _save_confirmed(repository, job)
    except ImageHistoryError:
        event("remote_ref_persistence_failed")
        raise
    event("provider_submit_accepted")
    event("remote_ref_saved")
    if submission.state is ProviderJobState.RUNNING:
        job = _save_confirmed(repository, _updated(job, status=JobStatus.RUNNING, started_at=now()))

    failure: JobError | None = None
    interrupted = None
    try:
        result = submission.result
        if submission.state is not ProviderJobState.COMPLETED:
            gateway = setup.gateway
            if not isinstance(gateway, PollingProviderGateway) or not gateway.capabilities.polling:
                raise ValueError("Async provider requires polling")
            assert ref is not None
            deadline = monotonic() + wait_timeout
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                await sleep(min(poll_interval, remaining))
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    state = await safe_get(lambda: gateway.get_status(ref), sleep=sleep)
                if monotonic() >= deadline:
                    raise TimeoutError
                if state is ProviderJobState.COMPLETED:
                    async with asyncio.timeout(deadline - monotonic()):
                        result = await safe_get(lambda: gateway.fetch_result(ref), sleep=sleep)
                    break
                if state in (ProviderJobState.FAILED, ProviderJobState.CANCELLED):
                    raise ProviderError(
                        JobError(code="REMOTE_GENERATION_FAILED", message="Remote image failed")
                    )
                if state is ProviderJobState.RUNNING and job.status is JobStatus.SUBMITTED:
                    job = _save_confirmed(
                        repository, _updated(job, status=JobStatus.RUNNING, started_at=now())
                    )
                    event("poll_state_changed")
        assert result is not None
        event("remote_completed")
        job = await finish_image_result(
            job,
            result,
            repository=repository,
            artifact_storage=artifact_storage,
            download=download,
            now=now,
            output_dir=output_dir,
            base_name=base_name,
            keep_original=keep_original,
            event=event,
        )
        if job.status is JobStatus.COMPLETED:
            event("job_completed")
        return job
    except ArtifactHistoryWriteError as exc:
        job = _append_artifact(job, exc.orphan)
        reconciled = _read_matching_completion(repository, job)
        if exc.interruption is not None:
            # Preserve the original interrupt and expose a safe forensic snapshot.
            # No failure write after a possibly committed final transaction.
            raise exc.interruption from ImageHistoryError(reconciled or job)
        if reconciled is None:
            raise ImageHistoryError(job) from None
        job = reconciled
        event("artifact_saved")
        event("job_completed")
        return job
    except ImageHistoryError:
        raise
    except ProviderError as exc:
        failure = _safe_error(exc.error, "Provider result failed")
    except TimeoutError:
        failure = JobError(code="JOB_TIMEOUT", message="Local image wait deadline exceeded")
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        if isinstance(exc.__cause__, ImageHistoryError):
            raise
        interrupted = exc
        failure = JobError(code="JOB_WAIT_INTERRUPTED", message="Local image wait interrupted")
    except Exception:
        failure = JobError(code="IMAGE_FINALIZATION_FAILED", message="Local image result failed")
    assert failure is not None
    try:
        job = _fail(repository, job, failure, now())
    except ImageHistoryError as exc:
        if interrupted is not None:
            raise interrupted from exc
        raise
    if interrupted is not None:
        raise interrupted
    return job


async def safe_get[T](
    operation: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Three bounded attempts for retryable GET only; never accepts submit."""
    for attempt in range(3):
        try:
            return await operation()
        except ProviderError as exc:
            if exc.error.retryable is not True or attempt == 2:
                raise
            await sleep(0.25 * (2**attempt))
    raise AssertionError("Unreachable")


async def finish_image_result(
    job: Job,
    result: ProviderResult,
    *,
    repository: JobRepository,
    artifact_storage: ArtifactStorage,
    download: Callable[[RemoteArtifact], Awaitable[bytes]],
    now: Callable[[], datetime] = _utc_now,
    output_dir: Path | None = None,
    base_name: str | None = None,
    keep_original: bool = False,
    recovery: bool = False,
    raise_on_failure: bool = False,
    event: Callable[[str], None] = lambda _: None,
) -> Job:
    """Billing before local work; persisted partial positions prevent duplicate files."""
    try:
        billed = _with_result(job, result)
        if billed != job:
            job = _save_confirmed(repository, billed)
        event("usage_recorded")
        if not result.remote_artifacts or any(
            item.kind is not ArtifactKind.IMAGE
            or not (item.url or item.base64_data or item.provider_file_id)
            for item in result.remote_artifacts
        ):
            raise ValueError("Remote result has no usable image")
        if len(result.remote_artifacts) < job.request.max_images:
            raise ProviderError(
                JobError(
                    code="PROVIDER_INCOMPLETE_RESULT",
                    message="Remote result has fewer images than requested",
                    retryable=True,
                )
            )
        prior = job.result or JobResult()
        # Legacy partial final files are in publication order; ORIGINAL is separate.
        finals = [a for a in prior.artifacts if a.role is ArtifactRole.FINAL]
        originals = [a for a in prior.artifacts if a.role is ArtifactRole.ORIGINAL]
        if len(finals) > len(result.remote_artifacts):
            raise ValueError("Remote artifact count changed")
        for index, locator in enumerate(result.remote_artifacts):
            if index < len(finals):
                if not artifact_storage.exists(finals[index]):
                    raise ValueError("Recorded partial artifact is missing")
                continue
            content = await download(locator)
            name = base_name
            if name and len(result.remote_artifacts) > 1:
                name = f"{name}_{index + 1:03d}"
            if keep_original and index >= len(originals):
                assert job.id is not None
                original = artifact_storage.save(
                    job_id=job.id,
                    content=content,
                    role=ArtifactRole.ORIGINAL,
                    output_dir=output_dir,
                    base_name=f"{name or 'result'}_original",
                )
                job = _save_confirmed(repository, _append_artifact(job, original))
            if index == len(result.remote_artifacts) - 1:
                completed = finalize_image_artifact(
                    job,
                    content=content,
                    repository=repository,
                    storage=artifact_storage,
                    completed_at=now(),
                    final_format=job.request.final_format,
                    output_dir=output_dir,
                    base_name=name,
                    recovery=recovery,
                )
                if completed.id is None or repository.get(completed.id) != completed:
                    raise ImageHistoryError(completed)
                event("artifact_saved")
                return completed
            assert job.id is not None
            artifact = artifact_storage.save(
                job_id=job.id,
                content=content,
                final_format=job.request.final_format,
                output_dir=output_dir,
                base_name=name,
            )
            job = _append_artifact(job, artifact)
            if not artifact_storage.exists(artifact):
                raise ValueError("Published image is not verified")
            job = _save_confirmed(repository, job)
            event("artifact_saved")
        # A prior final publication may be present in a FAILED Job after lost ack.
        from aimedia.domain.job import JobRecovery

        data = job.model_dump()
        ensure_transition(job.status, JobStatus.COMPLETED, recovery=recovery)
        data.update(status=JobStatus.COMPLETED, completed_at=now(), error=None)
        if recovery and job.error:
            data["recovery"] = JobRecovery(previous_error=job.error, recovered_at=now())
        return _save_confirmed(repository, Job.model_validate(data))
    except ArtifactHistoryWriteError as exc:
        job = _append_artifact(job, exc.orphan)
        reconciled = _read_matching_completion(repository, job, recovery=recovery)
        if exc.interruption is not None:
            raise exc.interruption from ImageHistoryError(reconciled or job)
        if reconciled is None:
            raise ImageHistoryError(job) from None
        return reconciled
    except ImageHistoryError:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        if isinstance(exc.__cause__, ImageHistoryError):
            raise
        try:
            failed = _fail(
                repository,
                job,
                JobError(code="JOB_WAIT_INTERRUPTED", message="Local image wait interrupted"),
                now(),
            )
        except ImageHistoryError as history:
            raise exc from history
        raise exc from ImageHistoryError(failed)
    except ProviderError as exc:
        error = _safe_error(exc.error, "Provider result failed")
    except Exception:
        error = JobError(code="IMAGE_FINALIZATION_FAILED", message="Local image result failed")
    failed = _fail(repository, job, error, now())
    if raise_on_failure:
        raise ImageAttemptFailed(failed, error)
    return failed


def _updated(job: Job, **fields: object) -> Job:
    status = fields.get("status")
    if isinstance(status, JobStatus):
        ensure_transition(job.status, status)
    return Job.model_validate({**job.model_dump(), **fields})


# Application persistence policy, not an adapter dependency. Only the documented
# ApiErrorBodyPresenter enum / ApiErrorMetadataPresenter example in
# docs/Post Media.txt may cross this boundary as provider diagnostics.
_SAFE_PROVIDER_CODES = frozenset(
    {
        "BAD_REQUEST",
        "UNAUTHORIZED",
        "api_key_revoked",
        "INSUFFICIENT_BALANCE",
        "FORBIDDEN",
        "NOT_FOUND",
        "REQUEST_TIMEOUT",
        "CONFLICT",
        "PAYLOAD_TOO_LARGE",
        "TOO_MANY_REQUESTS",
        "BAD_GATEWAY",
        "SERVICE_UNAVAILABLE",
        "INTERNAL_ERROR",
    }
)
_SAFE_PROVIDER_REASONS = frozenset({"noProvidersForModel"})


def _safe_error(error: JobError, message: str) -> JobError:
    code = error.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error.code) else "PROVIDER_ERROR"
    details: dict[str, object] = {}
    status = error.details.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        details["http_status"] = status
    reason = error.details.get("reason")
    if isinstance(reason, str) and reason in _SAFE_PROVIDER_REASONS:
        details["reason"] = reason
    provider_code = error.provider_code
    return JobError(
        code=code,
        message=message,
        provider_code=provider_code if provider_code in _SAFE_PROVIDER_CODES else None,
        retryable=None if code == "SUBMIT_UNCERTAIN" else error.retryable,
        details=details,
    )


def _with_result(job: Job, result: ProviderResult) -> Job:
    # Numeric billing extension fields survive; arbitrary strings/containers may
    # contain bodies, base64 or credentials and are intentionally not persisted.
    usage = result.usage
    if usage is not None:
        raw = redact(usage.raw)
        usage = Usage.model_validate(
            {
                **usage.model_dump(),
                "raw": {
                    key: value
                    for key, value in raw.items()
                    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                    and (value is None or type(value) in (int, float, bool, Decimal))
                },
            }
        )
        # Match the existing repository's JSON snapshot representation of raw
        # Decimal metrics; actual Cost remains typed Decimal at the top level.
        usage = Usage.model_validate(usage.model_dump(mode="json"))
    metadata = redact(result.provider_metadata)
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key in {"created_at", "completed_at", "warning_count"} and type(value) in (int, float)
    }
    prior = job.result or JobResult()
    return _updated(
        job,
        usage=usage if usage is not None else job.usage,
        cost=result.cost if result.cost is not None else job.cost,
        result=JobResult(artifacts=prior.artifacts, metadata={**prior.metadata, **safe_metadata}),
    )


def _append_artifact(job: Job, artifact: Artifact) -> Job:
    prior = job.result or JobResult()
    result = JobResult.model_validate(
        {**prior.model_dump(), "artifacts": (*prior.artifacts, artifact)}
    )
    return _updated(job, result=result)


def _save_confirmed(repository: JobRepository, expected: Job) -> Job:
    saved: Job | None = None
    try:
        saved = repository.save(expected)
        if expected.id is None:
            if saved.id is None or saved.id <= 0:
                saved = None
            else:
                expected = _updated(expected, id=saved.id)
        if saved == expected and repository.get(expected.id) == expected:  # type: ignore[arg-type]
            return expected
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Preserve a possibly committed billing/ref/partial snapshot just as for
        # final completion. An interrupt is not permission to write stale state.
        if expected.id is not None:
            try:
                persisted = repository.get(expected.id)
                if persisted == expected:
                    expected = persisted
            except Exception:
                pass
        raise exc from ImageHistoryError(expected)
    except Exception:
        pass
    # Only a known local ID can be reconciled. No second save/create/POST.
    if expected.id is not None:
        try:
            if repository.get(expected.id) == expected:
                return expected
        except Exception:
            pass
    raise ImageHistoryError(expected)


def _fail(repository: JobRepository, job: Job, error: JobError, completed_at: datetime) -> Job:
    # Never replace a committed terminal completion with an older local aggregate.
    # This is known-ID acknowledgement reconciliation, not recovery or a retry.
    try:
        persisted = repository.get(job.id)  # type: ignore[arg-type]
    except Exception:
        raise ImageHistoryError(job) from None
    if persisted is None:
        raise ImageHistoryError(job)
    if persisted.status is JobStatus.COMPLETED:
        reconciled = _read_matching_completion(repository, job)
        if reconciled is None:
            raise ImageHistoryError(persisted)
        return reconciled
    if job.status is JobStatus.FAILED:
        return _save_confirmed(repository, job)
    return _save_confirmed(
        repository, _updated(job, status=JobStatus.FAILED, error=error, completed_at=completed_at)
    )


def _read_matching_completion(
    repository: JobRepository, job: Job, *, recovery: bool = False
) -> Job | None:
    # Finalizer may have committed before losing its acknowledgement. Compare the
    # full expected aggregate, including all partial files and billing, not status.
    found: Job | None = None
    try:
        found = repository.get(job.id)  # type: ignore[arg-type]
    except Exception:
        pass
    if found is None or found.status is not JobStatus.COMPLETED:
        return None
    try:
        if job.status is not JobStatus.COMPLETED:
            ensure_transition(job.status, JobStatus.COMPLETED, recovery=recovery)
        if (
            recovery
            and job.error
            and (found.recovery is None or found.recovery.previous_error != job.error)
        ):
            return None
        expected = (
            job
            if job.status is JobStatus.COMPLETED
            else Job.model_validate(
                {
                    **job.model_dump(),
                    "status": JobStatus.COMPLETED,
                    "completed_at": found.completed_at,
                    "error": None,
                    "recovery": found.recovery if recovery else job.recovery,
                }
            )
        )
    except Exception:
        return None
    return found if found == expected else None
