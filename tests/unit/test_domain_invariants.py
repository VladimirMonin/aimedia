"""Инварианты домена: состояния Job, деньги unknown/zero, валидация requests.

Тесты доказывают доменное поведение без сети, SQLite и CLI: `Job` не создаётся в
противоречивом состоянии, terminal Job не возвращается в работу, ноль отличается
от неизвестной цены, а валюты не складываются между собой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aimedia.domain import (
    Artifact,
    ArtifactKind,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    DomainError,
    ImageGenerationRequest,
    InvalidJobStateTransitionError,
    Job,
    JobError,
    JobKind,
    JobRecovery,
    JobResult,
    JobStatus,
    ModelRef,
    ProviderRef,
    Usage,
    can_transition,
    ensure_transition,
    is_terminal,
    total_by_currency,
)

CREATED_AT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 9, 28, 8, 0, 21, tzinfo=UTC)

PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="seedream-5-pro")


def make_request(**overrides: object) -> ImageGenerationRequest:
    payload: dict[str, object] = {
        "provider": PROVIDER,
        "model": MODEL,
        "prompt": CompiledPrompt(text="A laboratory robot", source_count=1),
    }
    payload.update(overrides)
    return ImageGenerationRequest(**payload)  # type: ignore[arg-type]


def make_job(**overrides: object) -> Job:
    payload: dict[str, object] = {
        "kind": JobKind.IMAGE_GENERATE,
        "provider": PROVIDER,
        "model": MODEL,
        "request": make_request(),
        "created_at": CREATED_AT,
    }
    payload.update(overrides)
    return Job(**payload)  # type: ignore[arg-type]


def saved_image_artifact(path: str = "out/481/result_001.webp") -> Artifact:
    """Локально сохранённый image artifact — обязательный результат image Job."""
    return Artifact(kind=ArtifactKind.IMAGE, local_path=path)


def make_completed_image_job(**overrides: object) -> Job:
    """`completed` image Job с финальным result и сохранённым artifact."""
    payload: dict[str, object] = {
        "status": JobStatus.COMPLETED,
        "completed_at": COMPLETED_AT,
        "result": JobResult(artifacts=[saved_image_artifact()]),
    }
    payload.update(overrides)
    return make_job(**payload)


# --- Публичный контракт пакета домена ---------------------------------------


def test_every_public_export_is_importable() -> None:
    """Каждое имя из `aimedia.domain.__all__` реально доступно в пакете.

    `RemoteArtifact` был объявлен в `__all__`, но не импортирован, поэтому
    `from aimedia.domain import *` падал на нём. Тест ловит расхождение между
    списком экспорта и фактическими объектами модуля.
    """
    import aimedia.domain as domain

    missing = [name for name in domain.__all__ if not hasattr(domain, name)]
    assert missing == []


def test_remote_artifact_is_publicly_importable() -> None:
    """`RemoteArtifact` доступен как публичное имя пакета домена."""
    from aimedia.domain import RemoteArtifact

    remote = RemoteArtifact(kind=ArtifactKind.IMAGE, url="https://example.invalid/a.png")
    assert remote.kind is ArtifactKind.IMAGE
    assert remote.url == "https://example.invalid/a.png"


# --- Состояния Job -----------------------------------------------------------


def test_job_starts_created_without_terminal_timestamps() -> None:
    job = make_job()
    assert job.status is JobStatus.CREATED
    assert job.created_at == CREATED_AT
    assert job.submitted_at is None
    assert job.completed_at is None
    assert is_terminal(job.status) is False


def test_terminal_statuses_are_completed_failed_cancelled() -> None:
    assert is_terminal(JobStatus.COMPLETED)
    assert is_terminal(JobStatus.FAILED)
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.CREATED)
    assert not is_terminal(JobStatus.SUBMITTED)
    assert not is_terminal(JobStatus.RUNNING)


def test_terminal_job_requires_completed_at() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        make_job(status=JobStatus.COMPLETED)


def test_completed_job_may_not_carry_terminal_error() -> None:
    with pytest.raises(ValidationError, match="terminal error"):
        make_completed_image_job(error=JobError(code="X", message="boom"))


def test_completed_image_job_requires_final_result() -> None:
    """`completed` image Job без финального result не существует."""
    with pytest.raises(ValidationError, match="финальный result"):
        make_job(status=JobStatus.COMPLETED, completed_at=COMPLETED_AT)


def test_completed_image_job_requires_saved_local_artifact() -> None:
    """Успешный HTTP-response без сохранённого локального файла — ещё не completed."""
    remote_only = Artifact(
        kind=ArtifactKind.IMAGE,
        remote_url="https://example.invalid/result.png",
    )
    for result in (JobResult(), JobResult(artifacts=[remote_only]), JobResult(content="text")):
        with pytest.raises(ValidationError, match="локальный image artifact"):
            make_job(status=JobStatus.COMPLETED, completed_at=COMPLETED_AT, result=result)


@pytest.mark.parametrize("in_result", [True, False], ids=["result", "job"])
def test_completed_image_job_rejects_original_only(in_result: bool) -> None:
    """Сохранённый provider-original не заменяет конечный файл."""
    original = Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.ORIGINAL,
        local_path="out/481/original.webp",
    )
    with pytest.raises(ValidationError, match="ролью final"):
        make_completed_image_job(
            result=JobResult(artifacts=[original] if in_result else []),
            artifacts=[] if in_result else [original],
        )


def test_completed_image_job_accepts_saved_artifact_in_result() -> None:
    job = make_completed_image_job()
    assert job.result is not None
    assert job.artifact_paths == ()
    assert job.result.artifacts[0].local_path is not None


def test_completed_image_job_accepts_saved_artifact_in_aggregate_list() -> None:
    """Recovery-финализация кладёт сохранённый artifact в агрегатный список Job."""
    job = make_completed_image_job(
        result=JobResult(artifacts=[]),
        artifacts=[saved_image_artifact("out/481/recovered.webp")],
    )
    assert job.artifact_paths == ("out/481/recovered.webp",)


def test_completed_image_job_accepts_final_with_original_in_other_list() -> None:
    original = Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.ORIGINAL,
        local_path="out/481/original.webp",
    )
    job = make_completed_image_job(
        result=JobResult(artifacts=[original]),
        artifacts=[saved_image_artifact("out/481/final.webp")],
    )
    assert job.result is not None
    assert job.result.artifacts[0].role is ArtifactRole.ORIGINAL
    assert job.artifacts[0].role is ArtifactRole.FINAL


def test_recovery_failed_to_completed_supplies_saved_artifact() -> None:
    """Recovery-переход не ослабляется и требует сохранённый artifact.

    Failed image Job с partial result и cost разрешён, переход `failed → completed`
    возможен только с `recovery=True`, а итоговый completed Job обязан нести
    локально сохранённый artifact.
    """
    failed = make_job(
        status=JobStatus.FAILED,
        completed_at=COMPLETED_AT,
        error=JobError(code="ARTIFACT_DOWNLOAD_FAILED", message="download failed"),
        cost=Cost(amount="4.00", currency="RUB"),
    )
    assert failed.result is None
    assert failed.artifacts == []

    with pytest.raises(InvalidJobStateTransitionError):
        ensure_transition(failed.status, JobStatus.COMPLETED)
    assert ensure_transition(failed.status, JobStatus.COMPLETED, recovery=True) is (
        JobStatus.COMPLETED
    )

    # Без сохранённого artifact финализация не становится completed.
    with pytest.raises(ValidationError, match="локальный image artifact"):
        make_completed_image_job(result=JobResult())

    recovered = make_completed_image_job(
        cost=Cost(amount="4.00", currency="RUB"),
        recovery=JobRecovery(
            previous_error=JobError(code="ARTIFACT_DOWNLOAD_FAILED", message="download failed"),
            recovered_at=COMPLETED_AT,
        ),
    )
    assert recovered.status is JobStatus.COMPLETED
    assert recovered.error is None
    assert recovered.result is not None
    assert recovered.result.artifacts[0].local_path is not None


def test_failed_job_requires_error() -> None:
    with pytest.raises(ValidationError, match="обязан иметь error"):
        make_job(status=JobStatus.FAILED, completed_at=COMPLETED_AT)


def test_failed_job_keeps_partial_result_and_cost() -> None:
    """Неполная финализация не уничтожает уже известные usage/cost (R23)."""
    job = make_job(
        status=JobStatus.FAILED,
        completed_at=COMPLETED_AT,
        error=JobError(code="ARTIFACT_DOWNLOAD_FAILED", message="download failed"),
        usage=Usage(output_units=1.0),
        cost=Cost(amount="4.00", currency="RUB"),
        artifacts=[
            Artifact(
                kind=ArtifactKind.IMAGE,
                role=ArtifactRole.ORIGINAL,
                local_path="out/job-481/original.png",
            )
        ],
    )
    assert job.cost is not None
    assert job.has_known_cost is True
    assert job.error is not None
    assert len(job.artifacts) == 1


# --- Переходы состояний ------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.CREATED, JobStatus.SUBMITTED),
        (JobStatus.CREATED, JobStatus.FAILED),
        (JobStatus.CREATED, JobStatus.CANCELLED),
        (JobStatus.SUBMITTED, JobStatus.RUNNING),
        (JobStatus.SUBMITTED, JobStatus.COMPLETED),
        (JobStatus.SUBMITTED, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.COMPLETED),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.CANCELLED),
    ],
)
def test_standard_transitions_are_allowed(current: JobStatus, target: JobStatus) -> None:
    assert can_transition(current, target) is True
    assert ensure_transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.COMPLETED, JobStatus.RUNNING),
        (JobStatus.COMPLETED, JobStatus.SUBMITTED),
        (JobStatus.FAILED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.SUBMITTED),
        (JobStatus.CANCELLED, JobStatus.RUNNING),
        (JobStatus.CREATED, JobStatus.RUNNING),
        (JobStatus.CREATED, JobStatus.COMPLETED),
    ],
)
def test_forbidden_transitions_are_rejected(current: JobStatus, target: JobStatus) -> None:
    assert can_transition(current, target) is False
    with pytest.raises(InvalidJobStateTransitionError):
        ensure_transition(current, target)


def test_failed_to_completed_requires_recovery_flag() -> None:
    """Единственное исключение из terminal states — recovery того же remote job."""
    assert can_transition(JobStatus.FAILED, JobStatus.COMPLETED) is False
    assert can_transition(JobStatus.FAILED, JobStatus.COMPLETED, recovery=True) is True
    assert ensure_transition(JobStatus.FAILED, JobStatus.COMPLETED, recovery=True) is (
        JobStatus.COMPLETED
    )


def test_recovery_flag_does_not_open_intermediate_running() -> None:
    """Recovery не разрешает промежуточный возврат failed → running."""
    assert can_transition(JobStatus.FAILED, JobStatus.RUNNING, recovery=True) is False
    with pytest.raises(InvalidJobStateTransitionError):
        ensure_transition(JobStatus.FAILED, JobStatus.RUNNING, recovery=True)


def test_recovery_flag_does_not_reopen_completed_or_cancelled() -> None:
    for current in (JobStatus.COMPLETED, JobStatus.CANCELLED):
        for target in (JobStatus.RUNNING, JobStatus.SUBMITTED, JobStatus.FAILED):
            assert can_transition(current, target, recovery=True) is False


def test_invalid_transition_error_is_a_domain_error_with_details() -> None:
    with pytest.raises(InvalidJobStateTransitionError) as exc_info:
        ensure_transition(JobStatus.COMPLETED, JobStatus.RUNNING)
    error = exc_info.value
    assert isinstance(error, DomainError)
    assert error.code.value == "INVALID_JOB_STATE"
    assert error.details == {
        "current": "completed",
        "target": "running",
        "recovery": False,
    }
    stored = error.to_job_error(retryable=False)
    assert stored.code == "INVALID_JOB_STATE"
    assert stored.retryable is False


def test_recovery_record_keeps_previous_error() -> None:
    """Успешная финализация не стирает прежнюю ошибку: она остаётся в записи recovery."""
    job = make_completed_image_job(
        recovery=JobRecovery(
            previous_error=JobError(code="ARTIFACT_DOWNLOAD_FAILED", message="download failed"),
            recovered_at=COMPLETED_AT,
        ),
    )
    assert job.error is None
    assert job.recovery is not None
    assert job.recovery.previous_error.code == "ARTIFACT_DOWNLOAD_FAILED"


# --- Деньги: unknown ≠ zero, валюты не смешиваются ---------------------------


def test_zero_cost_is_known_and_differs_from_unknown() -> None:
    zero = Cost(amount=Decimal("0"), currency="RUB")
    assert zero.is_zero is True
    assert zero.amount == Decimal(0)

    # Отсутствие Cost — неизвестная цена, и это не то же состояние, что ноль.
    job_without_cost = make_job()
    assert job_without_cost.cost is None
    assert job_without_cost.has_known_cost is False

    job_with_zero_cost = make_job(cost=Cost(amount=Decimal("0.00"), currency="RUB"))
    assert job_with_zero_cost.has_known_cost is True
    assert job_with_zero_cost.cost is not None
    assert job_with_zero_cost.cost.is_zero is True


def test_cost_requires_currency_when_amount_is_known() -> None:
    with pytest.raises(ValidationError):
        Cost(amount=Decimal("4.00"))  # type: ignore[call-arg]


def test_cost_rejects_float_and_bool() -> None:
    """Двоичная дробь и булев флаг не являются денежной суммой."""
    for bad in (0.1, 0.0831, True):
        with pytest.raises(ValidationError):
            Cost(amount=bad, currency="USD")  # type: ignore[arg-type]


def test_cost_preserves_exact_decimal_without_float() -> None:
    cost = Cost(amount="0.0831", currency="usd")
    assert cost.amount == Decimal("0.0831")
    assert cost.currency == "USD"
    assert "0.0831" in cost.model_dump_json()


def test_exact_decimal_addition_avoids_binary_error() -> None:
    total = total_by_currency(
        [
            Cost(amount="0.1", currency="RUB"),
            Cost(amount="0.2", currency="RUB"),
        ]
    )
    assert total[0].amount == Decimal("0.3")
    assert total[0].job_count == 2


def test_currencies_are_not_summed_together() -> None:
    total = total_by_currency(
        [
            Cost(amount="0.1", currency="RUB"),
            Cost(amount="0.2", currency="RUB"),
            Cost(amount="1.00", currency="USD"),
        ]
    )
    assert [(entry.currency, entry.amount) for entry in total] == [
        ("RUB", Decimal("0.3")),
        ("USD", Decimal("1.00")),
    ]


def test_zero_costs_count_as_known_totals() -> None:
    """Известный ноль участвует в сумме, а неизвестная цена — нет."""
    total = total_by_currency([Cost(amount="0", currency="RUB")])
    assert total[0].amount == Decimal(0)
    assert total[0].job_count == 1


# --- Валидация requests и обязательных полей Job -----------------------------


def test_empty_compiled_prompt_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CompiledPrompt(text="   \n\n ", source_count=2)


def test_compiled_prompt_is_not_rewritten() -> None:
    """Компилятор не переписывает текст: значимые пробелы сохраняются."""
    text = "line one\n\n  indented  \n"
    assert CompiledPrompt(text=text, source_count=1).text == text


def test_max_images_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_request(max_images=0)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ImageGenerationRequest(
            kind="audio.transcribe",  # type: ignore[arg-type]
            provider=PROVIDER,
            model=MODEL,
            prompt=CompiledPrompt(text="x", source_count=1),
        )


def test_job_requires_model_and_request() -> None:
    """Job без модели или request не существует: nullability только по семантике."""
    for missing in ("model", "request", "provider", "kind", "created_at"):
        payload: dict[str, object] = {
            "kind": JobKind.IMAGE_GENERATE,
            "provider": PROVIDER,
            "model": MODEL,
            "request": make_request(),
            "created_at": CREATED_AT,
        }
        payload.pop(missing)
        with pytest.raises(ValidationError):
            Job(**payload)  # type: ignore[arg-type]


def test_job_remote_model_id_is_separate_from_logical_model() -> None:
    job = make_job(remote_model_id="provider/real-model-2026")
    assert job.model.id == "seedream-5-pro"
    assert job.remote_model_id == "provider/real-model-2026"


def test_frozen_models_reject_mutation() -> None:
    job = make_job()
    with pytest.raises(ValidationError):
        job.status = JobStatus.FAILED  # type: ignore[misc]


def test_extra_fields_are_not_silently_accepted() -> None:
    with pytest.raises(ValidationError):
        make_job(unexpected_field=1)


def test_created_job_may_not_carry_submission_timestamps() -> None:
    for field in ("submitted_at", "completed_at"):
        with pytest.raises(ValidationError, match="created"):
            make_job(**{field: COMPLETED_AT})


def test_artifact_paths_preserve_order_and_skip_missing_local_path() -> None:
    job = make_job(
        artifacts=[
            Artifact(kind=ArtifactKind.IMAGE, local_path="out/481/result_001.webp"),
            Artifact(kind=ArtifactKind.IMAGE),
            Artifact(
                kind=ArtifactKind.IMAGE,
                role=ArtifactRole.ORIGINAL,
                local_path="out/481/original.png",
            ),
        ]
    )
    assert job.artifact_paths == (
        "out/481/result_001.webp",
        "out/481/original.png",
    )
