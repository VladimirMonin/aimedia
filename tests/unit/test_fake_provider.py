"""Fake provider: сценарии success/pending/error без сети и production registry.

Тесты доказывают два разных утверждения:

1. fake реализует публичные порты домена (`ProviderGateway`,
   `PollingProviderGateway`, `CancellableProviderGateway`) с детерминированным
   поведением и считает фактические вызовы submit/fetch/cancel;
2. fake остаётся тестовым double: production-код его не импортирует, и он не
   регистрируется как production provider (проверяется
   `tests/architecture/test_dependencies.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import pytest
from fake_provider import (
    FAKE_PROVIDER_ID,
    FAKE_REMOTE_JOB_ID,
    FakeImageProvider,
    FakeScenario,
    FakeTransportError,
)

from aimedia.domain import (
    CancellableProviderGateway,
    CompiledPrompt,
    ImageGenerationRequest,
    ModelRef,
    PollingProviderGateway,
    ProviderCapabilities,
    ProviderError,
    ProviderGateway,
    ProviderJobState,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    SubmissionResult,
)

REQUEST = ImageGenerationRequest(
    provider=ProviderRef(id=FAKE_PROVIDER_ID),
    model=ModelRef(id="fake-model"),
    prompt=CompiledPrompt(text="robot", source_count=1),
)


def run(coro: Coroutine[object, object, object]) -> object:
    return asyncio.run(coro)


# --- Совместимость с портами -------------------------------------------------


def test_fake_satisfies_minimal_provider_gateway() -> None:
    provider = FakeImageProvider()
    assert isinstance(provider, ProviderGateway)
    assert provider.provider_id == FAKE_PROVIDER_ID


def test_fake_satisfies_polling_and_cancellable_protocols() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING)
    assert isinstance(provider, PollingProviderGateway)
    assert isinstance(provider, ProviderGateway)
    assert isinstance(provider, CancellableProviderGateway)


def test_real_adapter_missing_polling_is_not_a_fake_gateway_error() -> None:
    """Provider без опроса не обязан реализовывать заглушки через NotImplementedError.

    Ограничение выражено типом: объект с `submit` не проходит проверку
    `PollingProviderGateway`, а application смотрит на capabilities.
    """

    class SyncOnlyProvider:
        provider_id = "sync-only"

        @property
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities()

        async def submit(self, request: ImageGenerationRequest) -> SubmissionResult:
            return SubmissionResult(state=ProviderJobState.COMPLETED)

    provider = SyncOnlyProvider()
    assert isinstance(provider, ProviderGateway)
    assert not isinstance(provider, PollingProviderGateway)
    assert provider.capabilities.polling is False


# --- Сценарий success --------------------------------------------------------


def test_success_scenario_returns_completed_with_cost() -> None:
    provider = FakeImageProvider()
    result = run(provider.submit(REQUEST))
    assert isinstance(result, SubmissionResult)
    assert result.state is ProviderJobState.COMPLETED
    assert result.remote_ref is None
    assert result.result is not None
    assert result.result.cost is not None
    assert result.result.cost.amount == 4
    assert result.result.cost.currency == "RUB"
    assert result.result.remote_artifacts[0].url == "https://example.invalid/fake/result.png"
    assert provider.submit_count == 1


def test_success_scenario_keeps_raw_usage() -> None:
    provider = FakeImageProvider()
    result = run(provider.submit(REQUEST))
    assert result.result is not None
    assert result.result.usage is not None
    assert result.result.usage.raw["cost_rub"] == 4.0


def test_submitted_request_is_recorded_for_assertions() -> None:
    provider = FakeImageProvider()
    run(provider.submit(REQUEST))
    assert provider.submitted_requests == [REQUEST]


# --- Сценарий pending --------------------------------------------------------


def test_pending_scenario_returns_remote_ref_with_operation() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING)
    result = run(provider.submit(REQUEST))
    assert result.state is ProviderJobState.SUBMITTED
    assert result.result is None
    assert result.remote_ref is not None
    assert result.remote_ref.remote_job_id == FAKE_REMOTE_JOB_ID
    assert result.remote_ref.operation is RemoteOperation.MEDIA


def test_pending_scenario_completes_after_configured_polls() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING, pending_polls=2)
    run(provider.submit(REQUEST))
    ref = provider.remote_ref()
    assert run(provider.get_status(ref)) is ProviderJobState.RUNNING
    assert run(provider.get_status(ref)) is ProviderJobState.RUNNING
    assert run(provider.get_status(ref)) is ProviderJobState.COMPLETED
    fetched = run(provider.fetch_result(ref))
    assert fetched.remote_artifacts
    assert provider.fetch_count == 1


def test_pending_scenario_declares_async_capabilities() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING)
    assert provider.capabilities.async_jobs is True
    assert provider.capabilities.polling is True
    assert FakeImageProvider().capabilities.polling is False


def test_unknown_remote_ref_is_transport_error_not_silent_success() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING)
    foreign = RemoteJobRef(provider_id=FAKE_PROVIDER_ID, remote_job_id="other")
    with pytest.raises(FakeTransportError):
        run(provider.get_status(foreign))


# --- Сценарий error ----------------------------------------------------------


def test_error_scenario_raises_normalized_provider_error() -> None:
    provider = FakeImageProvider(FakeScenario.ERROR)
    with pytest.raises(ProviderError) as exc_info:
        run(provider.submit(REQUEST))
    error = exc_info.value.to_job_error()
    assert error.code == "REMOTE_GENERATION_FAILED"
    assert error.provider_code == "fake_rejected"
    assert error.retryable is False
    assert provider.submit_count == 1


def test_submit_transport_failure_propagates_before_recording_success() -> None:
    """Неизвестный outcome submit не превращается в успешный результат (R12)."""
    provider = FakeImageProvider(submit_error=FakeTransportError("connection reset"))
    with pytest.raises(FakeTransportError):
        run(provider.submit(REQUEST))
    assert provider.submit_count == 1


# --- Явная отмена ------------------------------------------------------------


def test_cancel_is_counted_and_records_the_exact_ref() -> None:
    provider = FakeImageProvider(FakeScenario.PENDING)
    ref = provider.remote_ref()
    run(provider.cancel(ref))
    assert provider.cancel_count == 1
    assert provider.cancelled_remote_refs == [ref]


def test_local_interruption_does_not_call_cancel() -> None:
    """Ctrl+C/timeout — локальное прекращение ожидания, а не remote cancel (R15)."""
    provider = FakeImageProvider(FakeScenario.PENDING)
    run(provider.submit(REQUEST))
    run(provider.get_status(provider.remote_ref()))
    assert provider.cancel_count == 0
    assert provider.cancelled_remote_refs == []
