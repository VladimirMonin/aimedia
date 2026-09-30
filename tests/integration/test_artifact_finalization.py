"""Финализация image Job: неподтверждённая история после публикации artifact.

Проверяется шов C08c2b (`src/aimedia/application/artifact_finalization.py`) на
реальных Peewee-репозитории, SQLite и файловом adapter в `tmp_path`:

- успешная финализация публикует проверенный финальный файл и пишет `completed`,
  сохраняя прежние usage/cost/metadata;
- отказ внутри транзакции `repository.save` после публикации оставляет файл
  и доказанно откатывает Job; отказ после commit оставляет completed в БД,
  но сообщает о неподтверждённом результате сохранения;
- typed-ошибка не переносит исходное DB-исключение в `__cause__`/`__context__` и в
  трассировку, опубликованный файл в пользовательском `--out` не удаляется;
- невозможное состояние до записи файла не создаёт файловых эффектов, а отказ
  публикации не превращается в `completed`;
- финализация не выполняет повторный remote submit: у неё вообще нет provider-порта.

Сеть и пользовательский data-root не затрагиваются: тесты работают с real
Pillow-байтами и временной SQLite.
"""

from __future__ import annotations

import asyncio
import hashlib
import traceback
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from fake_provider import FakeImageProvider, FakeScenario
from peewee import OperationalError
from PIL import Image

from aimedia.application.artifact_finalization import (
    ArtifactHistoryWriteError,
    finalize_image_artifact,
)
from aimedia.artifacts import ImageConversionError, PillowArtifactStorage
from aimedia.domain import (
    Artifact,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    FinalFormat,
    ImageGenerationRequest,
    InvalidJobStateTransitionError,
    Job,
    JobError,
    JobKind,
    JobResult,
    JobStatus,
    ModelRef,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    SubmissionResult,
    Usage,
)
from aimedia.storage import PeeweeJobRepository, open_database

DATABASE_FILENAME = "database.sqlite3"
CREATED_AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 10, 2, 9, 0, 7, tzinfo=UTC)

PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="synthetic-finalization-model")

# Канареечная строка: ни она, ни её части не должны попасть ни в `str`, ни в
# трассировку `ArtifactHistoryWriteError`.
DB_CANARY = "DB_CANARY_9f3a /abs/secret/orphan.png 'PROMPT CANARY c08c2b'"


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[PeeweeJobRepository]:
    """Реальный Peewee-репозиторий на временной SQLite; manager закрывается тестом."""
    manager = open_database(tmp_path / DATABASE_FILENAME)
    try:
        yield PeeweeJobRepository(manager)
    finally:
        manager.close()


@pytest.fixture
def storage(tmp_path: Path) -> PillowArtifactStorage:
    """Реальный файловый adapter с data-root в `tmp_path`."""
    return PillowArtifactStorage(data_root=tmp_path)


def _png(width: int = 4, height: int = 3) -> bytes:
    """Реальный PNG, который Pillow действительно декодирует и перекодирует."""
    buffer = BytesIO()
    Image.new("RGB", (width, height), (7, 8, 9)).save(buffer, "PNG")
    return buffer.getvalue()


def _request() -> ImageGenerationRequest:
    return ImageGenerationRequest(
        provider=PROVIDER,
        model=MODEL,
        prompt=CompiledPrompt(text="prompt cannotary", source_count=1, sha256="b" * 64),
        max_images=1,
    )


def _remote_ref() -> RemoteJobRef:
    return RemoteJobRef(
        provider_id="polza",
        remote_job_id="aig_c08c2b",
        operation=RemoteOperation.MEDIA,
    )


def _submitted_job() -> Job:
    """Сохранённый `submitted` image Job с прежними usage/cost/metadata."""
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.SUBMITTED,
        provider=PROVIDER,
        model=MODEL,
        request=_request(),
        remote_ref=_remote_ref(),
        result=JobResult(content="provider text", metadata={"trace_id": "trc_c08c2b"}),
        usage=Usage(output_units=1.0, raw={"cost_rub": 4.0}),
        cost=Cost(amount="4.00", currency="RUB"),
        created_at=CREATED_AT,
        submitted_at=CREATED_AT,
    )


def _created_job() -> Job:
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.CREATED,
        provider=PROVIDER,
        model=MODEL,
        request=_request(),
        created_at=CREATED_AT,
    )


def _cancelled_job() -> Job:
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.CANCELLED,
        provider=PROVIDER,
        model=MODEL,
        request=_request(),
        created_at=CREATED_AT,
        completed_at=COMPLETED_AT,
    )


def _persist(repository: PeeweeJobRepository, job: Job) -> Job:
    saved = repository.save(job)
    assert saved.id is not None
    return saved


def _traceback_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


class _ReturningJobRepository:
    """Возвращает неподтверждённое состояние вместо сохранённого completed."""

    def __init__(self, returned: Job | None) -> None:
        self.returned = returned
        self.save_calls = 0

    def save(self, job: Job) -> Job | None:
        self.save_calls += 1
        return self.returned


class _CommitThenRaiseRepository:
    """Записывает completed в настоящую SQLite, но теряет ответ после commit."""

    def __init__(self, repository: PeeweeJobRepository) -> None:
        self.repository = repository

    def save(self, job: Job) -> Job:
        self.repository.save(job)
        raise RuntimeError(DB_CANARY)


class _MalformedArtifactStorage:
    """Публикует настоящий файл, но возвращает испорченный DTO."""

    def __init__(self, storage: PillowArtifactStorage) -> None:
        self.storage = storage

    def preflight(self, *, job_id: int, output_dir: Path | None = None) -> None:
        self.storage.preflight(job_id=job_id, output_dir=output_dir)

    def save(
        self,
        *,
        job_id: int,
        content: bytes,
        role: ArtifactRole = ArtifactRole.FINAL,
        final_format: FinalFormat | None = None,
        source_mime_type: str | None = None,
        output_dir: Path | None = None,
        base_name: str | None = None,
    ) -> Artifact:
        artifact = self.storage.save(
            job_id=job_id,
            content=content,
            role=role,
            final_format=final_format,
            source_mime_type=source_mime_type,
            output_dir=output_dir,
            base_name=base_name,
        )
        return artifact.model_copy(update={"kind": DB_CANARY})


class _FailingJobRepository:
    """Fake-репозиторий, чей `save` отказывает канареечным исключением.

    Реализует минимальный `JobRepository`, чтобы проверка typed-ошибки не
    зависела от Peewee: даже произвольный сбой записи не должен утекать в
    трассировку финализации.
    """

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.save_calls = 0

    def save(self, job: Job) -> Job:
        self.save_calls += 1
        raise self._error

    def get(self, job_id: int) -> Job | None:  # pragma: no cover - не используется
        return None

    def list_recent(
        self,
        *,
        limit: int = 20,
        statuses: object = None,
    ) -> list[Job]:  # pragma: no cover - не используется
        return []


def _fail_save_via_get(repository: PeeweeJobRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сломать `save` после upsert: его внутренний `self.get` поднимает canary.

    Исключение возникает внутри `atomic("IMMEDIATE")`, поэтому транзакция обязана
    откатиться целиком, а опубликованный файл — остаться orphan.
    """

    def failing_get(job_id: int) -> Job | None:
        raise OperationalError(DB_CANARY)

    monkeypatch.setattr(repository, "get", failing_get)


def test_success_publishes_final_file_and_persists_completed(
    tmp_path: Path, repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    """Успех: финальный файл декодируется, completed записан, прежние поля целы."""
    persisted = _persist(repository, _submitted_job())
    result = finalize_image_artifact(
        persisted,
        content=_png(),
        repository=repository,
        storage=storage,
        completed_at=COMPLETED_AT,
        final_format=FinalFormat.WEBP,
    )
    restored = repository.get(persisted.id)

    assert result.status is JobStatus.COMPLETED
    assert result.id == persisted.id
    assert result.completed_at == COMPLETED_AT
    assert result.remote_ref == _remote_ref()
    assert result.usage is not None and result.usage.output_units == 1.0
    assert result.cost is not None and result.cost.amount == Decimal("4.00")
    assert result.result is not None
    assert result.result.content == "provider text"
    assert result.result.metadata == {"trace_id": "trc_c08c2b"}
    final = result.result.artifacts[0]
    assert final.role is ArtifactRole.FINAL
    assert final.mime_type == "image/webp"

    path = storage.resolve_path(final)
    published = path.read_bytes()
    assert published != _png()
    assert final.sha256 == hashlib.sha256(published).hexdigest()
    with Image.open(path) as image:
        image.load()
        assert image.format == "WEBP"

    assert restored is not None
    assert restored.status is JobStatus.COMPLETED
    assert restored.result is not None
    assert restored.result.artifacts[0].sha256 == final.sha256
    assert restored.result.metadata == {"trace_id": "trc_c08c2b"}


def test_db_failure_after_publication_leaves_orphan_and_nonterminal_job(
    repository: PeeweeJobRepository,
    storage: PillowArtifactStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ `save` после публикации: orphan на диске, Job остаётся non-terminal."""
    persisted = _persist(repository, _submitted_job())

    with monkeypatch.context() as patched:
        _fail_save_via_get(repository, patched)
        with pytest.raises(ArtifactHistoryWriteError) as excinfo:
            finalize_image_artifact(
                persisted,
                content=_png(),
                repository=repository,
                storage=storage,
                completed_at=COMPLETED_AT,
                final_format=FinalFormat.WEBP,
            )
    error = excinfo.value

    # Сбой не замаскирован успехом и не несёт исходного исключения.
    assert error.__cause__ is None
    assert error.__context__ is None
    text = _traceback_text(error)
    assert DB_CANARY not in text
    assert "/abs/secret/orphan.png" not in text
    assert "PROMPT CANARY" not in text
    assert error.orphan.local_path is not None
    assert str(error.orphan.local_path) not in text
    assert error.orphan.local_path.name not in text

    # Orphan диагностируем: файл существует, hash совпадает с метаданными.
    orphan_path = storage.resolve_path(error.orphan)
    assert orphan_path.exists()
    assert error.orphan.sha256 == hashlib.sha256(orphan_path.read_bytes()).hexdigest()

    # История не содержит ложного completed и не потеряла remote ref.
    restored = repository.get(persisted.id)
    assert restored is not None
    assert restored.status is JobStatus.SUBMITTED
    assert restored.completed_at is None
    assert restored.error is None
    assert restored.remote_ref == _remote_ref()
    assert restored.result is not None
    assert restored.result.metadata == {"trace_id": "trc_c08c2b"}

    # Никакой очистки: файл остаётся orphan до явного recovery, row ровно одна.
    assert orphan_path.exists()
    assert len(repository.list_recent()) == 1


def test_submitted_error_is_rejected_before_publication(
    tmp_path: Path, repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    persisted = _persist(
        repository,
        _submitted_job().model_copy(
            update={"error": JobError(code="TRANSIENT", message="previous failure")}
        ),
    )
    with pytest.raises(ValueError, match="terminal error"):
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=repository,
            storage=storage,
            completed_at=COMPLETED_AT,
        )
    assert not (tmp_path / "outputs").exists()
    restored = repository.get(persisted.id)
    assert restored is not None and restored.status is JobStatus.SUBMITTED


def test_malformed_artifact_after_publication_is_safe_typed_error(
    repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    persisted = _persist(repository, _submitted_job())
    with pytest.raises(ArtifactHistoryWriteError) as excinfo:
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=repository,
            storage=_MalformedArtifactStorage(storage),
            completed_at=COMPLETED_AT,
        )
    error = excinfo.value
    assert error.__cause__ is None and error.__context__ is None
    assert DB_CANARY not in str(error) + repr(error) + _traceback_text(error)
    assert error.orphan.kind == DB_CANARY
    assert storage.resolve_path(error.orphan).exists()
    restored = repository.get(persisted.id)
    assert restored is not None and restored.status is JobStatus.SUBMITTED


@pytest.mark.parametrize("wrong_field", ["id", "status"])
def test_repository_return_must_confirm_completed_same_id(
    wrong_field: str, repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    persisted = _persist(repository, _submitted_job())
    returned = persisted.model_copy(
        update={wrong_field: persisted.id + 1 if wrong_field == "id" else JobStatus.SUBMITTED}
    )
    fake = _ReturningJobRepository(returned)
    with pytest.raises(ArtifactHistoryWriteError) as excinfo:
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=fake,
            storage=storage,
            completed_at=COMPLETED_AT,
        )
    assert fake.save_calls == 1
    assert "не подтверждена" in str(excinfo.value)
    assert storage.resolve_path(excinfo.value.orphan).exists()
    restored = repository.get(persisted.id)
    assert restored is not None and restored.status is JobStatus.SUBMITTED


def test_commit_then_raise_reports_uncertainty_without_claiming_rollback(
    repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    persisted = _persist(repository, _submitted_job())
    with pytest.raises(ArtifactHistoryWriteError) as excinfo:
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=_CommitThenRaiseRepository(repository),
            storage=storage,
            completed_at=COMPLETED_AT,
        )
    error = excinfo.value
    assert error.__cause__ is None and error.__context__ is None
    assert DB_CANARY not in str(error) + repr(error) + _traceback_text(error)
    assert "не подтверждена" in str(error)
    assert "остаётся non-terminal" not in str(error)
    assert storage.resolve_path(error.orphan).exists()
    restored = repository.get(persisted.id)
    assert restored is not None and restored.status is JobStatus.COMPLETED
    assert restored.result is not None
    assert restored.result.artifacts[0].sha256 == error.orphan.sha256


def test_user_output_directory_is_untouched_on_history_failure(
    tmp_path: Path,
    repository: PeeweeJobRepository,
    storage: PillowArtifactStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Явный `--out`-каталог не создаётся и не чистится: файл остаётся на месте."""
    persisted = _persist(repository, _submitted_job())
    user_dir = tmp_path / "user_output"
    user_dir.mkdir()
    _fail_save_via_get(repository, monkeypatch)

    with pytest.raises(ArtifactHistoryWriteError) as excinfo:
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=repository,
            storage=storage,
            completed_at=COMPLETED_AT,
            final_format=FinalFormat.PNG,
            output_dir=user_dir,
            base_name="result",
        )

    error = excinfo.value
    published = sorted(user_dir.iterdir())
    assert [path.name for path in published] == ["result.png"]
    assert error.orphan.local_path == published[0]
    assert error.orphan.sha256 == hashlib.sha256(published[0].read_bytes()).hexdigest()
    assert not (tmp_path / "outputs").exists()


def test_fake_repository_failure_keeps_canary_and_cause_out_of_traceback(
    storage: PillowArtifactStorage,
) -> None:
    """Fake-репозиторий: typed-ошибка с orphan без утечки canary и cause/context."""
    persisted = _submitted_job().model_copy(update={"id": 1})
    repository = _FailingJobRepository(RuntimeError(DB_CANARY))

    with pytest.raises(ArtifactHistoryWriteError) as excinfo:
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=repository,
            storage=storage,
            completed_at=COMPLETED_AT,
            final_format=FinalFormat.WEBP,
        )

    error = excinfo.value
    assert repository.save_calls == 1
    assert error.__cause__ is None
    assert error.__context__ is None
    text = _traceback_text(error)
    assert DB_CANARY not in text
    assert "/abs/secret/orphan.png" not in text
    assert str(error.orphan.local_path) not in text
    orphan_path = storage.resolve_path(error.orphan)
    assert orphan_path.exists()
    assert error.orphan.sha256 == hashlib.sha256(orphan_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("job_factory", [_created_job, _cancelled_job])
def test_impossible_state_before_write_has_no_file_side_effect(
    tmp_path: Path,
    repository: PeeweeJobRepository,
    storage: PillowArtifactStorage,
    job_factory: Callable[[], Job],
) -> None:
    """`created`/terminal Job не публикуют файл: preflight выполняется до записи."""
    persisted = _persist(repository, job_factory())
    with pytest.raises(InvalidJobStateTransitionError):
        finalize_image_artifact(
            persisted,
            content=_png(),
            repository=repository,
            storage=storage,
            completed_at=COMPLETED_AT,
            final_format=FinalFormat.PNG,
        )
    assert not (tmp_path / "outputs").exists()
    restored = repository.get(persisted.id)
    assert restored is not None
    assert restored.status is persisted.status


def test_publish_failure_does_not_become_completed(
    tmp_path: Path, repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    """Отказ публикации (недоступные байты) не создаёт файл и не пишет completed."""
    persisted = _persist(repository, _submitted_job())
    with pytest.raises(ImageConversionError):
        finalize_image_artifact(
            persisted,
            content=b"not an image at all",
            repository=repository,
            storage=storage,
            completed_at=COMPLETED_AT,
            final_format=FinalFormat.PNG,
        )
    assert not (tmp_path / "outputs").exists()
    restored = repository.get(persisted.id)
    assert restored is not None
    assert restored.status is JobStatus.SUBMITTED
    assert restored.completed_at is None


def test_finalization_does_not_resubmit_remote_execution(
    repository: PeeweeJobRepository, storage: PillowArtifactStorage
) -> None:
    """У финализации нет provider-порта: счётчик submit не меняется."""
    provider = FakeImageProvider(scenario=FakeScenario.PENDING)
    submission: SubmissionResult = asyncio.run(provider.submit(_request()))
    assert submission.remote_ref is not None
    assert provider.submit_count == 1

    persisted = _persist(
        repository,
        _submitted_job().model_copy(update={"remote_ref": submission.remote_ref}),
    )
    finalized = finalize_image_artifact(
        persisted,
        content=_png(),
        repository=repository,
        storage=storage,
        completed_at=COMPLETED_AT,
        final_format=FinalFormat.WEBP,
    )

    assert finalized.status is JobStatus.COMPLETED
    assert provider.submit_count == 1
    assert provider.fetch_count == 0
    assert provider.cancel_count == 0
    assert provider.submitted_requests[0].prompt.text == "prompt cannotary"
