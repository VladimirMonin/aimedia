"""Финализация image Job: публикация artifact и запись `completed` в историю.

Модуль закрывает узкий шов E05 между файловым adapter и историей Job
(`docs/plans/README.md`, E05: «При ошибке БД после записи файла остаётся
диагностируемый orphan, а не ложный `completed`»; `docs/plans/08-job-execution.md`,
«Failure после artifact save, но до DB commit»).

Порядок операций:

1. preflight до любого файлового эффекта: сохранённый положительный `job.id`,
   kind `image.generate`, отсутствие terminal error, допустимый переход в
   `completed` (только из `submitted`/`running`) и timezone-aware `completed_at`;
2. `storage.save` публикует **реальный** финальный файл и возвращает `Artifact`,
   описывающий конечные байты;
3. из исходного агрегата строится валидированный `completed` Job с финальным
   result/artifact; прежние usage/cost/metadata сохраняются;
4. `repository.save` пишет историю одной транзакцией.

Если после публикации построение completed Job или подтверждение записи истории
не удалось (в том числе `DatabaseBusyError`), поднимается
`ArtifactHistoryWriteError` с опубликованным `Artifact` и **без** исходного
исключения в цепочке. Файл не удаляется. Итог транзакции может быть неизвестен:
файл может быть orphan либо уже записан в completed Job. Вызывающий слой обязан
сверить историю перед recovery; повторный submit не производится.

Модуль не импортирует storage, artifacts, provider или logging: работа идёт
только через доменные порты `JobRepository`/`ArtifactStorage`, поэтому у шва нет
ни повторного provider submit, ни собственного диагностического канала. Ремонт
orphan и полный recovery — задача E07/E08, а не этого модуля.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aimedia.domain.artifacts import Artifact, ArtifactRole
from aimedia.domain.job import Job, JobResult
from aimedia.domain.ports import ArtifactStorage, JobRepository
from aimedia.domain.requests import FinalFormat, JobKind
from aimedia.domain.state import JobStatus, ensure_transition


class ArtifactHistoryWriteError(Exception):
    """Файл опубликован; завершение Job в истории не подтверждено вызывающему.

    Сообщение и трассировка не содержат исходного исключения, пути, имени файла
    или prompt. `orphan` предоставляет фактически опубликованный Artifact для
    сверки с БД: он может оказаться orphan либо уже записанным в completed Job.
    Файл не удаляется, состояние Job здесь не утверждается.
    """

    def __init__(self, orphan: Artifact) -> None:
        super().__init__(
            "Запись completed Job в историю не подтверждена; опубликованный artifact "
            "может быть orphan или уже записан в истории — требуется сверка"
        )
        self.orphan = orphan


def finalize_image_artifact(
    job: Job,
    *,
    content: bytes,
    repository: JobRepository,
    storage: ArtifactStorage,
    completed_at: datetime,
    final_format: FinalFormat | None = None,
    output_dir: Path | None = None,
    base_name: str | None = None,
) -> Job:
    """Опубликовать финальный artifact и записать `completed` Job.

    `job` — уже сохранённый `submitted`/`running` image Job с `id`; его usage,
    cost и result-metadata переносятся в финальный агрегат без изменений. При
    невозможном состоянии до записи файла не создаётся ни одного файлового
    эффекта. После публикации неподтверждённая запись или ошибка сборки Job
    поднимает `ArtifactHistoryWriteError`; файл не удаляется. Запись могла
    завершиться до ошибки ответа, поэтому статус в БД нужно сверить.

    `output_dir` — явный пользовательский каталог (уже существует), при его
    отсутствии storage использует managed `outputs/<job_id>`.
    """
    job_id = _preflight(job, completed_at)
    artifact = storage.save(
        job_id=job_id,
        content=content,
        role=ArtifactRole.FINAL,
        final_format=final_format,
        output_dir=output_dir,
        base_name=base_name,
    )
    saved: Job | None = None
    try:
        completed = _completed_job(job, artifact=artifact, completed_at=completed_at)
        saved = repository.save(completed)
        confirmed = saved is not None and saved.id == job_id and saved.status is JobStatus.COMPLETED
    except Exception:
        # Включая ValidationError после публикации, DB-ошибку и сбой проверки
        # ответа. BaseException (Ctrl+C) намеренно не глотается.
        confirmed = False
    if confirmed:
        assert saved is not None
        return saved
    # Поднятие вне `except` исключает исходную ошибку из cause/context/traceback.
    # Отсутствие подтверждения не означает доказанный rollback транзакции.
    raise ArtifactHistoryWriteError(artifact)


def _preflight(job: Job, completed_at: datetime) -> int:
    """Проверить состояние и идентификаторы до любого файлового эффекта."""
    if job.kind is not JobKind.IMAGE_GENERATE:
        raise ValueError("Финализация artifact поддерживает только image.generate Job")
    if type(job.id) is not int or job.id <= 0:
        raise ValueError("Финализация требует сохранённый Job с положительным id")
    if not isinstance(completed_at, datetime) or completed_at.utcoffset() is None:
        raise ValueError("completed_at должен быть timezone-aware timestamp")
    ensure_transition(job.status, JobStatus.COMPLETED)
    if job.error is not None:
        raise ValueError("Job с terminal error нельзя завершить без отдельного recovery")
    return job.id


def _completed_job(job: Job, *, artifact: Artifact, completed_at: datetime) -> Job:
    """Построить валидированный `completed` Job, сохранив прежние поля агрегата."""
    return Job.model_validate(
        {
            **job.model_dump(),
            "status": JobStatus.COMPLETED,
            "completed_at": completed_at,
            "result": _completed_result(job.result, artifact),
        }
    )


def _completed_result(prior: JobResult | None, artifact: Artifact) -> JobResult:
    """Финальный result: прежние artifacts/content/metadata плюс новый artifact."""
    if prior is None:
        return JobResult(artifacts=(artifact,))
    return prior.model_copy(update={"artifacts": (*prior.artifacts, artifact)})


__all__ = ["ArtifactHistoryWriteError", "finalize_image_artifact"]
