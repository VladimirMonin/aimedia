"""Порты домена: контракты provider и storage, реализуемые снаружи.

Port — контракт, который нужен приложению, но реализуется адаптером
(`02-system-architecture.md`). Домен зависит от этих протоколов, а adapters
(`PolzaProviderGateway`, `PeeweeJobRepository`, `LocalArtifactStorage`) зависят от
домена, но не наоборот.

Методов-заглушек здесь нет: provider, не умеющий опрашивать async-задание или
отменять его, не обязан реализовывать `NotImplementedError`. Такие возможности
описаны отдельными протоколами и gate-флагом `ProviderCapabilities`, поэтому
application проверяет наличие возможности, а не ловит исключение.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import model_validator

from aimedia.domain.artifacts import Artifact, ArtifactKind, ArtifactRole, RemoteArtifact
from aimedia.domain.base import DomainModel
from aimedia.domain.costs import Cost, CostReport, Usage
from aimedia.domain.job import Job
from aimedia.domain.refs import (
    ProviderCapabilities,
    ProviderJobState,
    RemoteJobRef,
)
from aimedia.domain.requests import FinalFormat, ImageGenerationRequest
from aimedia.domain.state import JobStatus


class ProviderResult(DomainModel):
    """Нормализованный ответ provider.

    Сырой HTTP JSON не пересекает эту границу: adapter приводит ответ к доменным
    значениям, сохраняя незнакомые показатели в `provider_metadata` и `usage.raw`,
    чтобы диагностическая информация не терялась.
    """

    remote_artifacts: list[RemoteArtifact] = []
    content: str | None = None
    usage: Usage | None = None
    cost: Cost | None = None
    provider_metadata: dict[str, Any] = {}


class SubmissionResult(DomainModel):
    """Результат `submit()`, нормализующий синхронный и асинхронный API.

    Синхронный provider возвращает `completed` вместе с результатом, асинхронный —
    `submitted` с `remote_ref` для последующего опроса. Нормализованный отказ
    provider поднимается как `ProviderError` с `JobError`, а не возвращается особым
    значением результата: ошибка не должна выглядеть успешным ответом.
    """

    state: ProviderJobState
    remote_ref: RemoteJobRef | None = None
    result: ProviderResult | None = None

    @model_validator(mode="after")
    def _check_submission_state(self) -> Self:
        if self.state is ProviderJobState.COMPLETED and (
            self.result is None
            or not any(
                artifact.kind is ArtifactKind.IMAGE
                and (artifact.url or artifact.base64_data or artifact.provider_file_id)
                for artifact in self.result.remote_artifacts
            )
        ):
            raise ValueError("completed submit требует результат с доступным image artifact")
        if self.state in (ProviderJobState.SUBMITTED, ProviderJobState.RUNNING) and (
            self.remote_ref is None or self.remote_ref.operation is None
        ):
            raise ValueError("ожидающий submit требует remote_ref с operation")
        if self.state is ProviderJobState.FAILED:
            raise ValueError("отказ provider при submit должен поднимать ProviderError")
        return self


@runtime_checkable
class ProviderGateway(Protocol):
    """Минимальный контракт provider: приём задания.

    `provider_id` обязан совпадать с `ProviderRef.id`, по которому application
    выбрала adapter, иначе задание уйдёт не туда.
    """

    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def submit(self, request: ImageGenerationRequest) -> SubmissionResult: ...


@runtime_checkable
class PollingProviderGateway(ProviderGateway, Protocol):
    """Provider, чьё удалённое задание требует опроса статуса."""

    async def get_status(self, remote_ref: RemoteJobRef) -> ProviderJobState: ...

    async def fetch_result(self, remote_ref: RemoteJobRef) -> ProviderResult: ...


@runtime_checkable
class CancellableProviderGateway(ProviderGateway, Protocol):
    """Provider, поддерживающий отмену удалённого задания.

    Локальное прекращение ожидания (Ctrl+C, timeout) не вызывает `cancel`:
    отдельный протокол нужен именно для явной отмены, подтверждённой provider.
    """

    async def cancel(self, remote_ref: RemoteJobRef) -> None: ...


@runtime_checkable
class JobRepository(Protocol):
    """Хранение истории Job.

    Наружу отдаются доменные объекты, а не Peewee-записи: repository преобразует
    `Peewee record ↔ domain object` (`07-storage-history-costs.md`). Job aggregate
    несёт inputs и prompt sources, поэтому отдельных repository для них в порте нет.

    Методы синхронны: SQLite локальна, а долгое ожидание remote не выполняется
    внутри транзакции.
    """

    def save(self, job: Job) -> Job:
        """Создать или обновить Job и вернуть сохранённое состояние с `id`."""
        ...

    def get(self, job_id: int) -> Job | None:
        """Прочитать Job по локальному ID; `None`, если такого Job нет."""
        ...

    def list_recent(
        self,
        *,
        limit: int = 20,
        statuses: Sequence[JobStatus] | None = None,
    ) -> Sequence[Job]:
        """Вернуть последние Job, при необходимости только с указанными статусами.

        Используется командой `jobs recent` и поиском незавершённых заданий для
        `jobs sync`.
        """
        ...


@runtime_checkable
class CostReportRepository(Protocol):
    """Прочитать расходы по сохранённым Job, не изменяя историю."""

    def aggregate(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> CostReport:
        """Суммы по валютам за UTC-интервал создания Job [start, end)."""
        ...


@runtime_checkable
class ArtifactStorage(Protocol):
    """Сохранение результата, полученного от provider.

    Adapter сам выбирает безопасный путь, пишет байты, проверяет фактический файл
    и выполняет разрешённую локальную конвертацию, а возвращает `Artifact` с
    метаданными конечных байтов (решения baseline D08/D09, этап E05).
    Незавершённая запись не должна выглядеть успешным артефактом.
    """

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
        """Сохранить байты как артефакт и вернуть метаданные фактического файла."""
        ...

    def exists(self, artifact: Artifact) -> bool:
        """Существует ли файл артефакта (история остаётся и после его удаления)."""
        ...

    def resolve_path(self, artifact: Artifact) -> Path:
        """Вернуть фактический путь артефакта."""
        ...
