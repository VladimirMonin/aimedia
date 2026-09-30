"""Согласованная сериализация домена: Decimal, UTC, enum, Paths.

Тесты доказывают, что JSON-контракт домена однозначен и обратим: сумма уходит
строкой (без float), timestamp — ISO-8601 UTC с `Z`, enum — строковым значением,
Path — posix-строкой, одинаковой на Windows и Linux. Round trip не теряет смысл
денежных и временных значений.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimedia.domain import (
    Artifact,
    ArtifactKind,
    ArtifactRole,
    CompiledPrompt,
    Cost,
    FinalFormat,
    ImageGenerationRequest,
    InputKind,
    InputRef,
    Job,
    JobKind,
    JobResult,
    JobStatus,
    ModelRef,
    ProviderJobState,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
    Sha256Hex,
    Usage,
    UtcDatetime,
)

CREATED_AT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
SHA256 = "0" * 64

PROVIDER = ProviderRef(id="polza")
MODEL = ModelRef(id="seedream-5-pro")


def make_job(**overrides: object) -> Job:
    request = ImageGenerationRequest(
        provider=PROVIDER,
        model=MODEL,
        prompt=CompiledPrompt(text="robot", source_count=1),
        images=[
            InputRef(
                kind=InputKind.IMAGE,
                path=Path("refs/robot.png"),
                position=0,
                sha256=SHA256,
            )
        ],
        aspect_ratio="16:9",
        final_format=FinalFormat.WEBP,
        seed=42,
    )
    payload: dict[str, object] = {
        "kind": JobKind.IMAGE_GENERATE,
        "provider": PROVIDER,
        "model": MODEL,
        "request": request,
        "created_at": CREATED_AT,
    }
    payload.update(overrides)
    return Job(**payload)  # type: ignore[arg-type]


# --- Деньги -----------------------------------------------------------------


def test_decimal_is_serialized_as_string_not_float() -> None:
    payload = json.loads(Cost(amount="0.0831", currency="RUB").model_dump_json())
    assert payload == {"amount": "0.0831", "currency": "RUB"}
    assert isinstance(payload["amount"], str)


def test_decimal_json_round_trip_preserves_trailing_zeros() -> None:
    cost = Cost(amount=Decimal("4.00"), currency="RUB")
    restored = Cost.model_validate_json(cost.model_dump_json())
    assert restored == cost
    assert restored.amount == Decimal("4.00")


def test_decimal_never_uses_exponent_notation() -> None:
    """`Decimal("1E+2")` не превращается в `1E+2`: сумма читаема и точна."""
    cost = Cost(amount=Decimal("1E+2"), currency="RUB")
    assert json.loads(cost.model_dump_json())["amount"] == "100"
    assert Cost.model_validate_json(cost.model_dump_json()).amount == Decimal("100")


def test_decimal_accepts_text_and_rejects_float() -> None:
    assert Cost(amount="0.1", currency="RUB").amount == Decimal("0.1")
    for bad in (0.1, object(), True, "not-a-number"):
        with pytest.raises(ValidationError):
            Cost(amount=bad, currency="RUB")  # type: ignore[arg-type]


def test_non_finite_decimal_is_rejected() -> None:
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValidationError):
            Cost(amount=bad, currency="RUB")


def test_currency_is_normalized_to_upper_case() -> None:
    assert Cost(amount="1", currency="rub").currency == "RUB"
    with pytest.raises(ValidationError):
        Cost(amount="1", currency="RU")


# --- Timestamps --------------------------------------------------------------


def test_utc_timestamp_serializes_with_z_suffix() -> None:
    job = make_job()
    payload = json.loads(job.model_dump_json())
    assert payload["created_at"] == "2026-09-28T08:00:00Z"


def test_timestamp_is_converted_to_utc_from_offset() -> None:
    shifted = datetime(2026, 9, 28, 11, 0, tzinfo=timezone(timedelta(hours=3)))
    job = make_job(created_at=shifted)
    assert json.loads(job.model_dump_json())["created_at"] == "2026-09-28T08:00:00Z"
    assert job.created_at == CREATED_AT


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_job(created_at=datetime(2026, 9, 28, 8, 0))


def test_microseconds_survive_json_round_trip() -> None:
    precise = datetime(2026, 9, 28, 8, 0, 1, 120000, tzinfo=UTC)
    job = make_job(created_at=precise)
    text = job.model_dump_json()
    assert "2026-09-28T08:00:01.120000Z" in text
    assert Job.model_validate_json(text) == job


# --- Paths, enums, hashes ----------------------------------------------------


def test_path_serializes_as_posix_on_any_platform() -> None:
    artifact = Artifact(
        kind=ArtifactKind.IMAGE,
        local_path=Path("outputs") / "481" / "result_001.webp",
    )
    payload = json.loads(artifact.model_dump_json())
    assert payload["local_path"] == "outputs/481/result_001.webp"
    assert "\\" not in payload["local_path"]


def test_managed_path_serializes_as_posix_and_legacy_input_stays_valid() -> None:
    """Managed-путь уходит posix-строкой, а legacy-вход без копии остаётся валидным."""
    managed = InputRef(
        kind=InputKind.IMAGE,
        path=Path("refs/robot.png"),
        position=0,
        mime_type="image/png",
        size_bytes=4096,
        sha256=SHA256,
        managed_path=Path("inputs") / "481" / "0.png",
    )
    payload = json.loads(managed.model_dump_json())
    assert payload["managed_path"] == "inputs/481/0.png"
    assert "\\" not in payload["managed_path"]
    assert payload["path"] == "refs/robot.png"
    assert InputRef.model_validate_json(managed.model_dump_json()) == managed

    # Legacy-запись без managed-копии: поле отсутствует как значение и не мешает
    # чтению истории, сохранённой до schema v2.
    legacy = InputRef(kind=InputKind.IMAGE, path=Path("refs/robot.png"), position=0)
    legacy_payload = json.loads(legacy.model_dump_json())
    assert legacy_payload["managed_path"] is None
    assert InputRef.model_validate_json(legacy.model_dump_json()) == legacy


def test_artifact_role_distinguishes_original_and_final() -> None:
    original = Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.ORIGINAL,
        local_path=Path("out/original.png"),
    )
    final = Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.FINAL,
        local_path=Path("out/final.webp"),
    )
    assert original.role is ArtifactRole.ORIGINAL
    assert final.role is ArtifactRole.FINAL
    assert original != final
    assert json.loads(original.model_dump_json())["role"] == "original"


def test_enums_serialize_as_declared_strings() -> None:
    job = make_job()
    payload = json.loads(job.model_dump_json())
    assert payload["kind"] == "image.generate"
    assert payload["status"] == "created"
    assert payload["request"]["final_format"] == "webp"
    assert payload["request"]["images"][0]["kind"] == "image"


def test_remote_ref_keeps_operation_and_remote_job_id() -> None:
    ref = RemoteJobRef(
        provider_id="polza",
        remote_job_id="aig_abc123",
        operation=RemoteOperation.MEDIA,
    )
    payload = json.loads(ref.model_dump_json())
    assert payload == {
        "provider_id": "polza",
        "remote_job_id": "aig_abc123",
        "operation": "media",
    }
    assert RemoteJobRef.model_validate_json(ref.model_dump_json()) == ref


def test_remote_ref_operation_is_optional_but_not_invented() -> None:
    ref = RemoteJobRef(provider_id="polza", remote_job_id="gen_1")
    assert ref.operation is None
    assert json.loads(ref.model_dump_json())["operation"] is None


def test_sha256_is_validated_and_normalized() -> None:
    ref = InputRef(kind=InputKind.IMAGE, path="a.png", position=0, sha256="A" * 64)
    assert ref.sha256 == "a" * 64
    for bad in ("a" * 63, "z" * 64, "not-a-hash"):
        with pytest.raises(ValidationError):
            InputRef(kind=InputKind.IMAGE, path="a.png", position=0, sha256=bad)


# --- Полный round trip -------------------------------------------------------


def test_full_job_round_trip_python_and_json() -> None:
    job = make_job(
        id=481,
        status=JobStatus.COMPLETED,
        completed_at=datetime(2026, 9, 28, 8, 0, 21, tzinfo=UTC),
        remote_ref=RemoteJobRef(
            provider_id="polza",
            remote_job_id="aig_abc123",
            operation=RemoteOperation.MEDIA,
        ),
        cost=Cost(amount="4.00", currency="RUB"),
        usage=Usage(output_units=1.0, raw={"cost_rub": 4.0}),
        result=JobResult(
            artifacts=[Artifact(kind=ArtifactKind.IMAGE, local_path="out/481/result_002.webp")]
        ),
        artifacts=[
            Artifact(
                kind=ArtifactKind.IMAGE,
                mime_type="image/WebP",
                size_bytes=1854921,
                local_path=Path("out/481/result_001.webp"),
            )
        ],
    )
    payload = json.loads(job.model_dump_json())
    assert isinstance(payload["artifacts"], list)
    assert isinstance(payload["result"]["artifacts"], list)
    assert isinstance(job.artifacts, tuple)
    assert job.result is not None
    assert isinstance(job.result.artifacts, tuple)
    for restored in (
        Job.model_validate(job.model_dump(mode="python")),
        Job.model_validate_json(job.model_dump_json()),
        Job.model_validate(json.loads(job.model_dump_json())),
    ):
        assert restored == job
        assert restored.cost is not None
        assert restored.cost.amount == Decimal("4.00")
        assert restored.created_at.tzinfo is not None
        assert restored.artifacts[0].mime_type == "image/webp"
        assert isinstance(restored.artifacts, tuple)
        assert restored.result is not None
        assert isinstance(restored.result.artifacts, tuple)


def test_mime_type_is_validated_and_normalized() -> None:
    artifact = Artifact(kind=ArtifactKind.IMAGE, mime_type="image/WebP")
    assert artifact.mime_type == "image/webp"
    for bad in ("image", "image/", "/png", "not a mime"):
        with pytest.raises(ValidationError):
            Artifact(kind=ArtifactKind.IMAGE, mime_type=bad)


def test_usage_preserves_raw_provider_fields() -> None:
    usage = Usage(output_units=1.0, raw={"output_units": 1.0, "cost_rub": 4.0})
    restored = Usage.model_validate_json(usage.model_dump_json())
    assert restored.raw == {"output_units": 1.0, "cost_rub": 4.0}
    assert restored.output_units == 1.0


def test_provider_job_state_covers_normalized_states() -> None:
    assert {state.value for state in ProviderJobState} == {
        "submitted",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_utc_datetime_annotation_rejects_naive_input() -> None:
    """Отдельная проверка аннотации: правило действует и вне моделей Job."""
    from pydantic import BaseModel, ConfigDict

    class Holder(BaseModel):
        model_config = ConfigDict(frozen=True)
        at: UtcDatetime

    with pytest.raises(ValidationError):
        Holder(at=datetime(2026, 9, 28, 8, 0))
    assert Holder(at=CREATED_AT).at == CREATED_AT


def test_sha256_annotation_normalizes_case() -> None:
    from pydantic import BaseModel, ConfigDict

    class Holder(BaseModel):
        model_config = ConfigDict(frozen=True)
        digest: Sha256Hex

    assert Holder(digest="AB" * 32).digest == "ab" * 32
