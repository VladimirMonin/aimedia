"""Управляемый fake provider для offline-тестов domain/application.

Этот модуль живёт в `tests/support` и **не является** production provider: он не
импортируется ни одним модулем `aimedia` и не регистрируется в production registry
(`docs/plans/README.md`, E02: «Fake provider не подменяет реальный Polza adapter в
production registry»). Каталог добавляется в `sys.path` только из
`tests/conftest.py`.

Fake реализует публичные порты как настоящий adapter: приём задания
(`ProviderGateway`), опрос async-задания (`PollingProviderGateway`) и отмену
(`CancellableProviderGateway`). Сценарии success/pending/error детерминированы: ни
сети, ни времени, ни случайности. Транспортный сбой моделируется исключением
`FakeTransportError`, а нормализованный отказ provider — `ProviderError`, как и у
реального adapter.
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.artifacts import ArtifactKind, RemoteArtifact
from aimedia.domain.costs import Cost, Usage
from aimedia.domain.errors import JobError, ProviderError
from aimedia.domain.ports import ProviderResult, SubmissionResult
from aimedia.domain.refs import (
    ProviderCapabilities,
    ProviderJobState,
    RemoteJobRef,
    RemoteOperation,
)
from aimedia.domain.requests import ImageGenerationRequest

FAKE_PROVIDER_ID = "fake"
FAKE_REMOTE_JOB_ID = "fake_job_1"


class FakeScenario(StrEnum):
    """Заранее заданный исход работы fake provider."""

    SUCCESS = "success"
    PENDING = "pending"
    ERROR = "error"


class FakeTransportError(Exception):
    """Сбой самого вызова, а не нормализованный отказ provider.

    Например «POST принят, соединение оборвано»: outcome неизвестен, поэтому
    application не имеет права автоматически повторять submit (требование R12).
    """


class FakeImageProvider:
    """Fake provider изображений с детерминированными сценариями.

    `submit_count`, `fetch_count` и `cancel_count` позволяют доказать, что
    запрещённое действие (повторный submit при recovery, remote cancel при Ctrl+C)
    действительно не выполнялось.
    """

    def __init__(
        self,
        scenario: FakeScenario = FakeScenario.SUCCESS,
        *,
        pending_polls: int = 1,
        provider_id: str = FAKE_PROVIDER_ID,
        remote_job_id: str = FAKE_REMOTE_JOB_ID,
        submit_error: Exception | None = None,
    ) -> None:
        self._scenario = scenario
        self._pending_polls = pending_polls
        self._provider_id = provider_id
        self._remote_job_id = remote_job_id
        self._submit_error = submit_error
        self._polls = 0
        self.submit_count = 0
        self.fetch_count = 0
        self.cancel_count = 0
        self.cancelled_remote_refs: list[RemoteJobRef] = []
        self.submitted_requests: list[ImageGenerationRequest] = []

    @property
    def scenario(self) -> FakeScenario:
        return self._scenario

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            async_jobs=self._scenario is FakeScenario.PENDING,
            polling=self._scenario is FakeScenario.PENDING,
            cancellation=True,
        )

    def remote_ref(self) -> RemoteJobRef:
        """Ссылка на удалённое задание fake provider."""
        return RemoteJobRef(
            provider_id=self._provider_id,
            remote_job_id=self._remote_job_id,
            operation=RemoteOperation.MEDIA,
        )

    async def submit(self, request: ImageGenerationRequest) -> SubmissionResult:
        """Принять задание согласно выбранному сценарию."""
        self.submit_count += 1
        self.submitted_requests.append(request)
        if self._submit_error is not None:
            raise self._submit_error
        if self._scenario is FakeScenario.ERROR:
            raise ProviderError(
                JobError(
                    code="REMOTE_GENERATION_FAILED",
                    message="Fake provider отклонил задание.",
                    provider_code="fake_rejected",
                    provider_message="fake rejection",
                    retryable=False,
                )
            )
        if self._scenario is FakeScenario.PENDING:
            return SubmissionResult(
                state=ProviderJobState.SUBMITTED,
                remote_ref=self.remote_ref(),
            )
        return SubmissionResult(
            state=ProviderJobState.COMPLETED,
            result=self._completed_result(),
        )

    async def get_status(self, remote_ref: RemoteJobRef) -> ProviderJobState:
        """Вернуть `running` первые `pending_polls` опросов, затем `completed`."""
        self._ensure_known_ref(remote_ref)
        self._polls += 1
        if self._polls <= self._pending_polls:
            return ProviderJobState.RUNNING
        return ProviderJobState.COMPLETED

    async def fetch_result(self, remote_ref: RemoteJobRef) -> ProviderResult:
        """Вернуть нормализованный результат после завершения."""
        self._ensure_known_ref(remote_ref)
        self.fetch_count += 1
        return self._completed_result()

    async def cancel(self, remote_ref: RemoteJobRef) -> None:
        """Зафиксировать явную отмену, подтверждённую provider."""
        self.cancel_count += 1
        self.cancelled_remote_refs.append(remote_ref)

    def _ensure_known_ref(self, remote_ref: RemoteJobRef) -> None:
        if remote_ref.remote_job_id != self._remote_job_id:
            raise FakeTransportError(f"Неизвестное удалённое задание: {remote_ref.remote_job_id}")

    def _completed_result(self) -> ProviderResult:
        return ProviderResult(
            remote_artifacts=[
                RemoteArtifact(
                    kind=ArtifactKind.IMAGE,
                    url="https://example.invalid/fake/result.png",
                    content_type="image/png",
                )
            ],
            usage=Usage(output_units=1.0, raw={"output_units": 1.0, "cost_rub": 4.0}),
            cost=Cost(amount="4.00", currency="RUB"),
            provider_metadata={"model": "fake-model"},
        )
