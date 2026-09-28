"""Ссылки домена: provider, модель, удалённая операция и её состояние.

Ни одна из этих структур не содержит секретов, URL adapter или объектов
`httpx` (`03-domain-model.md`, «ProviderRef не содержит secrets»). Единое
каноническое имя удалённого задания — `remote_job_id` (решение baseline D10).
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.base import DomainModel, NonEmptyStr


class ProviderRef(DomainModel):
    """Идентификатор provider как доменное понятие, а не HTTP-клиент."""

    id: NonEmptyStr


class ModelRef(DomainModel):
    """Ссылка на логическую модель; remote ID живёт в binding."""

    id: NonEmptyStr


class ProviderModelBinding(DomainModel):
    """Разрешение логической модели у конкретного provider.

    Создаётся Model Registry (E03); `remote_model_id` — то, что реально уходит
    provider. YAML хранит bindings и ограничения, а mapping в параметры API
    выполняет Python adapter (решение baseline D07).
    """

    provider_id: NonEmptyStr
    remote_model_id: NonEmptyStr
    options: dict[str, object] = {}


class RemoteOperation(StrEnum):
    """Тип удалённой операции.

    Endpoint по строке ID не угадывается: операция сохраняется вместе с
    `remote_job_id` (решение baseline D10). Для image-модуля v0.1 существует
    единственное значение; новые типы добавляются вместе с новой функциональностью.
    """

    MEDIA = "media"


class RemoteJobRef(DomainModel):
    """Ссылка на асинхронное задание provider."""

    provider_id: NonEmptyStr
    remote_job_id: NonEmptyStr
    operation: RemoteOperation | None = None


class ProviderJobState(StrEnum):
    """Нормализованное состояние удалённого задания.

    Provider-specific статусы (`pending`, `queued`, `rendering`) приводит к этой
    модели adapter, а не application layer (`05-provider-system.md`).
    """

    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderCapabilities(DomainModel):
    """Возможности provider, а не модели.

    Способ доставки входных изображений (base64/URL) и прочие детали конкретного
    API добавляются тем этапом, который их использует: домен не описывает
    несуществующее поведение заранее.
    """

    async_jobs: bool = False
    polling: bool = False
    cancellation: bool = False
