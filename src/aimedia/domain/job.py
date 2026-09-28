"""Центральный агрегат `Job` и его результат.

`Job` — один логический запуск одной AI-задачи. Внутренний `Job.id` независим от
provider: удалённое задание живёт в `RemoteJobRef` и не смешивается с локальным ID
(инвариант 3). Retry создаёт новый Job со ссылкой на исходный, а не переписывает
старый (инвариант 12).

Границы агрегата: `Job`, request, result, remote ref и error. Бинарные файлы
физически находятся вне агрегата — `Artifact` хранит путь и метаданные, а не байты.
"""

from __future__ import annotations

from pydantic import model_validator

from aimedia.domain.artifacts import Artifact
from aimedia.domain.base import DomainModel, UtcDatetime
from aimedia.domain.costs import Cost, Usage
from aimedia.domain.errors import JobError
from aimedia.domain.inputs import CompiledPrompt, InputRef, PromptSource
from aimedia.domain.refs import ModelRef, ProviderRef, RemoteJobRef
from aimedia.domain.requests import AnyJobRequest, JobKind
from aimedia.domain.state import JobStatus, is_terminal


class JobRelation(DomainModel):
    """Связь нового Job с исходным.

    В v0.1 существует только `retry_of`: остальные типы (variation/upscale)
    добавляются вместе с соответствующей функциональностью.
    """

    parent_job_id: int
    type: str = "retry_of"


class JobRecovery(DomainModel):
    """Запись recovery того же remote execution.

    Единственное исключение из инварианта terminal states — `failed → completed`
    при recovery без новой генерации. Прежняя ошибка не стирается, а сохраняется
    здесь, поэтому успешная финализация не маскирует прежний сбой.
    """

    previous_error: JobError
    recovered_at: UtcDatetime


class JobResult(DomainModel):
    """Нормализованный результат выполнения.

    Для file-producing Job постоянным результатом считается локально сохранённый
    `Artifact`; `content` покрывает текстовый ответ модели.
    """

    artifacts: list[Artifact] = []
    content: str | None = None
    usage: Usage | None = None
    cost: Cost | None = None
    metadata: dict[str, object] = {}


class Job(DomainModel):
    """Один запуск одной AI-задачи.

    Job нельзя создать без kind, provider, модели, request и статуса `created`:
    `model = None` для Job, который без модели не существует, недопустим.
    """

    id: int | None = None

    kind: JobKind
    status: JobStatus = JobStatus.CREATED

    provider: ProviderRef
    model: ModelRef
    # Фактический ID у provider фиксируется в истории отдельно от логического:
    # provider может сменить модель под тем же alias (решение baseline D06).
    remote_model_id: str | None = None

    request: AnyJobRequest

    inputs: list[InputRef] = []
    prompt_sources: list[PromptSource] = []
    compiled_prompt: CompiledPrompt | None = None

    result: JobResult | None = None
    artifacts: list[Artifact] = []

    usage: Usage | None = None
    cost: Cost | None = None

    remote_ref: RemoteJobRef | None = None
    error: JobError | None = None

    relation: JobRelation | None = None
    recovery: JobRecovery | None = None

    created_at: UtcDatetime
    submitted_at: UtcDatetime | None = None
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check_state_invariants(self) -> Job:
        """Проверить инварианты состояний, не зависящие от конкретного перехода.

        Creation: статус `created` не имеет времени подачи и завершения.
        Completion: `completed` не может нести terminal error. Failure: `failed`
        обязан иметь error. Terminal Job всегда имеет `completed_at`.
        """
        if self.status is JobStatus.CREATED and (
            self.submitted_at is not None or self.completed_at is not None
        ):
            raise ValueError("Job в статусе created не может иметь submitted_at/completed_at")
        if self.status is JobStatus.COMPLETED and self.error is not None:
            raise ValueError("Job в статусе completed не может нести terminal error")
        if self.status is JobStatus.FAILED and self.error is None:
            raise ValueError("Job в статусе failed обязан иметь error")
        if is_terminal(self.status) and self.completed_at is None:
            raise ValueError("terminal Job обязан иметь completed_at")
        return self

    @property
    def has_known_cost(self) -> bool:
        """Известна ли фактическая стоимость.

        Неизвестная цена (`cost is None`) — не то же самое, что известный ноль.
        """
        return self.cost is not None

    @property
    def artifact_paths(self) -> tuple[str, ...]:
        """Пути локальных артефактов в порядке их добавления."""
        return tuple(
            artifact.local_path.as_posix()
            for artifact in self.artifacts
            if artifact.local_path is not None
        )
