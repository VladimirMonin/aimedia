"""CN-01 archive/confirmation seam, not a Job runner or submit service.

Caller reads snapshots BEFORE initial Job creation (D03). This hook accepts only
an already confirmed CREATED Job. Required model validation is invoked after ID
confirmation and before any publication; E07 owns its composition and failed-Job
transition. No provider calls, retry, source reread or deletion happen here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from aimedia.application.inputs.prepare import ReferenceSnapshot, validate_reference_content
from aimedia.domain.inputs import InputRef
from aimedia.domain.job import Job
from aimedia.domain.ports import JobRepository, ManagedInputStorage
from aimedia.domain.requests import ImageGenerationRequest, JobKind
from aimedia.domain.state import JobStatus


class InputArchiveError(Exception):
    """Archive not confirmed: retain published files and reconcile known Job.

    published contains links returned by the adapter, not a claim of rollback
    or an exhaustive orphan inventory (a failing save may have published too).
    No source exception, prompt or path is included in the message/traceback.
    """

    def __init__(self, job_id: int, published: Sequence[InputRef]) -> None:
        super().__init__("Managed inputs are not confirmed; submit is forbidden")
        self.job_id = job_id
        self.published = tuple(published)


def archive_reference_images(
    job: Job,
    snapshots: Sequence[ReferenceSnapshot],
    *,
    repository: JobRepository,
    storage: ManagedInputStorage,
    validate_model: Callable[[ImageGenerationRequest], object],
) -> ImageGenerationRequest:
    """Publish all refs, confirm all DB links, then return a temporary request.

    Persisted paths stay provenance; only returned request.images paths point
    at verified absolute copies. A save exception is reconciled by one get of
    the known ID; no successful complete match means stop without retry/cleanup.
    Calling this hook again on already archived inputs is forbidden.
    """
    # Reject bool/coercible IDs before model revalidation can turn them into int.
    if type(job.id) is not int or job.id <= 0:
        raise ValueError("Archive requires a positive confirmed job_id")
    job = Job.model_validate(job.model_dump())
    refs = [snapshot.ref for snapshot in snapshots]
    if (
        job.kind is not JobKind.IMAGE_GENERATE
        or job.status is not JobStatus.CREATED
        or job.error is not None
        or job.remote_ref is not None
        or job.inputs != refs
        or job.request.images != refs
        or job.provider != job.request.provider
        or job.model != job.request.model
        or any(
            ref.position != index or ref.managed_path is not None for index, ref in enumerate(refs)
        )
    ):
        raise ValueError("Archive requires a confirmed CREATED image Job and matching snapshots")
    assert job.id is not None
    job_id = job.id
    if repository.get(job_id) != job:
        raise ValueError("Job creation is not confirmed")
    for snapshot in snapshots:
        validate_reference_content(snapshot.ref, snapshot.content)
    # Separate request prevents a validator mutating the history snapshot.
    validate_model(ImageGenerationRequest.model_validate(job.request.model_dump()))

    published: list[InputRef] = []
    ready: ImageGenerationRequest | None = None
    try:
        for snapshot in snapshots:
            copied = storage.save(job_id=job_id, ref=snapshot.ref, content=snapshot.content)
            published.append(copied)
            if (
                copied.managed_path is None
                or copied.managed_path.parts[:2] != ("inputs", str(job_id))
                or InputRef.model_validate({**copied.model_dump(), "managed_path": None})
                != snapshot.ref
            ):
                raise ValueError("Copy adapter returned inconsistent reference")
        request = ImageGenerationRequest.model_validate(
            {**job.request.model_dump(), "images": published}
        )
        archived = Job.model_validate({**job.model_dump(), "inputs": published, "request": request})
        saved: Job | None
        try:
            saved = repository.save(archived)
        except Exception:
            # Commit may have succeeded; reconcile only known ID, never save again.
            saved = repository.get(job_id)
        if saved != archived or repository.get(job_id) != archived:
            raise ValueError("Complete archive links are not confirmed")
        transport_refs = []
        for ref in published:
            path = storage.resolve_path(ref)
            if not path.is_absolute():
                raise ValueError("Transport copy path must be absolute")
            transport_refs.append(InputRef.model_validate({**ref.model_dump(), "path": path}))
        ready = ImageGenerationRequest.model_validate(
            {**request.model_dump(), "images": transport_refs}
        )
    except Exception:
        # Raise outside except to suppress unsafe original exception context.
        pass
    if ready is None:
        raise InputArchiveError(job_id, published)
    return ready
