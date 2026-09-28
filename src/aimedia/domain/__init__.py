"""Домен aimedia: понятия, инварианты и контракты без инфраструктуры.

Пакет не импортирует Typer, Rich, httpx, Peewee, YAML, Pillow или platformdirs,
не выполняет IO и не зависит от настроек и секретов (инвариант 5 в
`docs/plans/03-domain-model.md`; проверяется `tests/architecture/test_dependencies.py`).

Что здесь есть (C03): Job и value objects, типизированные requests/results,
денежные значения с различием unknown и zero, доменные ошибки, допустимые переходы
состояний, provider/storage ports.

Файловый pipeline C04 (компиляция prompt и подготовка входов: порядок, MIME,
SHA-256) живёт отдельно — в `aimedia.application`, а не в домене. Домен определяет
только DTO под него (`PromptSource`, `CompiledPrompt`, `InputRef`) и остаётся
IO-free.
"""

from __future__ import annotations

from aimedia.domain.artifacts import Artifact, ArtifactKind, ArtifactRole, RemoteArtifact
from aimedia.domain.base import (
    CurrencyCode,
    DomainModel,
    ExactDecimal,
    LocalPath,
    MimeType,
    NonBlankStr,
    NonEmptyStr,
    Sha256Hex,
    UtcDatetime,
)
from aimedia.domain.costs import Cost, CurrencyTotal, Usage, total_by_currency
from aimedia.domain.errors import (
    DomainError,
    DomainErrorCode,
    InputFileNotFoundError,
    InvalidJobStateTransitionError,
    InvalidParameterValueError,
    JobError,
    PromptRequiredError,
    ProviderError,
    TooManyReferenceImagesError,
    UnknownModelError,
    UnknownProviderError,
    UnsupportedCapabilityError,
    UnsupportedInputFormatError,
    UnsupportedParameterError,
)
from aimedia.domain.inputs import (
    CompiledPrompt,
    InputKind,
    InputRef,
    PromptSource,
    PromptSourceKind,
)
from aimedia.domain.job import Job, JobRecovery, JobRelation, JobResult
from aimedia.domain.ports import (
    ArtifactStorage,
    CancellableProviderGateway,
    JobRepository,
    PollingProviderGateway,
    ProviderGateway,
    ProviderResult,
    SubmissionResult,
)
from aimedia.domain.refs import (
    ModelRef,
    ProviderCapabilities,
    ProviderJobState,
    ProviderModelBinding,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
)
from aimedia.domain.requests import (
    AnyJobRequest,
    FinalFormat,
    ImageGenerationRequest,
    JobKind,
    JobRequest,
)
from aimedia.domain.state import (
    TERMINAL_STATUSES,
    JobStatus,
    can_transition,
    ensure_transition,
    is_terminal,
)

__all__ = [
    "TERMINAL_STATUSES",
    "AnyJobRequest",
    "Artifact",
    "ArtifactKind",
    "ArtifactRole",
    "ArtifactStorage",
    "CancellableProviderGateway",
    "CompiledPrompt",
    "Cost",
    "CurrencyCode",
    "CurrencyTotal",
    "DomainError",
    "DomainErrorCode",
    "DomainModel",
    "ExactDecimal",
    "FinalFormat",
    "ImageGenerationRequest",
    "InputFileNotFoundError",
    "InputKind",
    "InputRef",
    "InvalidJobStateTransitionError",
    "InvalidParameterValueError",
    "Job",
    "JobError",
    "JobKind",
    "JobRecovery",
    "JobRelation",
    "JobRepository",
    "JobRequest",
    "JobResult",
    "JobStatus",
    "LocalPath",
    "MimeType",
    "ModelRef",
    "NonBlankStr",
    "NonEmptyStr",
    "PollingProviderGateway",
    "PromptRequiredError",
    "PromptSource",
    "PromptSourceKind",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderGateway",
    "ProviderJobState",
    "ProviderModelBinding",
    "ProviderRef",
    "ProviderResult",
    "RemoteArtifact",
    "RemoteJobRef",
    "RemoteOperation",
    "Sha256Hex",
    "SubmissionResult",
    "TooManyReferenceImagesError",
    "UnknownModelError",
    "UnknownProviderError",
    "UnsupportedCapabilityError",
    "UnsupportedInputFormatError",
    "UnsupportedParameterError",
    "Usage",
    "UtcDatetime",
    "can_transition",
    "ensure_transition",
    "is_terminal",
    "total_by_currency",
]
