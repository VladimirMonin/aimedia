"""Контрактные тесты чистой нормализации ответов Polza Media (E06, C09b).

Проверяется преобразование уже разобранного JSON `POST /v1/media` и
`GET /v1/media/{id}` в доменные `SubmissionResult`/`ProviderJobState`/`ProviderResult`
через `aimedia.providers.polza.media.response`. Ни HTTP-клиента, ни auth, ни
загрузки файлов: они относятся к C09c.

Все fixtures синтетические. Форма конверта описана в `docs/Get Media.txt`
(схема `MediaStatusPresenter`), но реальные model ID и живые ответы здесь не
подтверждаются. Форма `data` как списка объектов с `url` не подтверждена
документацией и помечена как synthetic. Offline-guard из `tests/conftest.py`
блокирует любой внешний сокет; отдельный тест доказывает, что модуль не тянет
транспорт.
"""

from __future__ import annotations

import ast
import traceback
from decimal import Decimal
from pathlib import Path

import pytest

from aimedia.domain import (
    ArtifactKind,
    ProviderError,
    ProviderJobState,
    RemoteOperation,
)
from aimedia.providers.polza.media import (
    POLZA_PROVIDER_ID,
    normalize_media_result,
    normalize_media_status,
    normalize_media_submission,
)

REMOTE_ID = "aig_synthetic"
IMAGE_URL = "https://cdn.example.invalid/aig_synthetic.jpg"
SYNTHETIC_MODEL = "synthetic/model-id"

RESPONSE_MODULE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "aimedia"
    / "providers"
    / "polza"
    / "media"
    / "response.py"
)


def _payload(**overrides: object) -> dict[str, object]:
    """Документированный конверт `MediaStatusPresenter` с синтетическими значениями."""
    payload: dict[str, object] = {
        "id": REMOTE_ID,
        "object": "media.generation",
        "status": "completed",
        "created": 1703001244,
        "model": SYNTHETIC_MODEL,
        "data": {"url": IMAGE_URL},
    }
    payload.update(overrides)
    return payload


def test_pending_becomes_submitted_with_remote_ref() -> None:
    """`pending` → `SUBMITTED` с сохранённым remote ref и operation MEDIA."""
    submission = normalize_media_submission(_payload(status="pending", data=None))
    assert submission.state is ProviderJobState.SUBMITTED
    assert submission.result is None
    assert submission.remote_ref is not None
    assert submission.remote_ref.provider_id == POLZA_PROVIDER_ID
    assert submission.remote_ref.remote_job_id == REMOTE_ID
    assert submission.remote_ref.operation is RemoteOperation.MEDIA


def test_processing_becomes_running() -> None:
    """`processing` → `RUNNING` c remote ref."""
    submission = normalize_media_submission(_payload(status="processing", data=None))
    assert submission.state is ProviderJobState.RUNNING
    assert submission.remote_ref is not None
    assert submission.remote_ref.remote_job_id == REMOTE_ID


def test_status_polling_maps_documented_states() -> None:
    assert normalize_media_status(_payload(status="pending", data=None)) is (
        ProviderJobState.SUBMITTED
    )
    assert normalize_media_status(_payload(status="processing", data=None)) is (
        ProviderJobState.RUNNING
    )
    assert normalize_media_status(_payload(status="completed")) is ProviderJobState.COMPLETED


def test_synchronous_completed_submission_carries_result() -> None:
    """Синхронный completed `POST` даёт `COMPLETED` с разобранным результатом."""
    submission = normalize_media_submission(_payload())
    assert submission.state is ProviderJobState.COMPLETED
    assert submission.result is not None
    artifacts = submission.result.remote_artifacts
    assert len(artifacts) == 1
    assert artifacts[0].kind is ArtifactKind.IMAGE
    assert artifacts[0].url == IMAGE_URL


def test_completed_result_normalization_is_same_shape() -> None:
    """`GET` completed нормализуется тем же способом, что синхронный `POST`."""
    result = normalize_media_result(_payload())
    assert [artifact.url for artifact in result.remote_artifacts] == [IMAGE_URL]


def test_multiple_artifacts_synthetic_list_shape() -> None:
    """SYNTHETIC: список `data` с несколькими `url` даёт несколько image artifacts.

    Эта форма не описана в `docs/Get Media.txt`; тест фиксирует только согласованное
    расширение на несколько артефактов и не выдаёт его за документированный контракт.
    """
    payload = _payload(data=[{"url": IMAGE_URL}, {"url": IMAGE_URL + ".2"}])
    result = normalize_media_result(payload)
    assert [artifact.url for artifact in result.remote_artifacts] == [
        IMAGE_URL,
        IMAGE_URL + ".2",
    ]
    assert all(artifact.kind is ArtifactKind.IMAGE for artifact in result.remote_artifacts)


def test_cancelled_is_typed_non_success() -> None:
    """`cancelled` — нормализованное не-success состояние, а не ошибка."""
    assert normalize_media_status(_payload(status="cancelled", data=None)) is (
        ProviderJobState.CANCELLED
    )
    submission = normalize_media_submission(_payload(status="cancelled", data=None))
    assert submission.state is ProviderJobState.CANCELLED


def test_failed_raises_normalized_provider_error_without_raw_text() -> None:
    """`failed` поднимает `ProviderError` без сырого текста provider."""
    canary = "secret-provider-canary"
    payload = _payload(
        status="failed",
        data=None,
        error={
            "code": "BAD_GATEWAY",
            "message": f"Ошибка генерации: {canary}",
            "metadata": {"raw": canary, "provider_name": "synthetic-provider"},
        },
    )
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)

    error = excinfo.value.error
    assert error.code == "REMOTE_GENERATION_FAILED"
    assert error.provider_code == "BAD_GATEWAY"
    assert error.provider_message is None
    assert error.details == {"provider_name": "synthetic-provider"}
    formatted = "".join(traceback.format_exception(excinfo.value))
    assert canary not in formatted
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


@pytest.mark.parametrize("field", ["code", "provider_name"])
@pytest.mark.parametrize(
    "unsafe",
    [
        "https://cdn.example.invalid/x?sig=secret-provider-canary",
        "prompt secret-provider-canary",
        "x" * 129,
    ],
    ids=["signed-url", "prompt", "oversize"],
)
def test_failed_drops_unsafe_diagnostic_identifiers(field: str, unsafe: str) -> None:
    """Сырой текст в любом диагностическом поле не попадает в историю и traceback."""
    error_body: dict[str, object] = {
        "code": "BAD_GATEWAY",
        "metadata": {"provider_name": "synthetic-provider"},
    }
    if field == "code":
        error_body["code"] = unsafe
    else:
        error_body["metadata"] = {"provider_name": unsafe}
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(_payload(status="failed", data=None, error=error_body))
    error = excinfo.value.to_job_error()
    assert error.provider_code == ("BAD_GATEWAY" if field != "code" else None)
    assert error.details == (
        {"provider_name": "synthetic-provider"} if field != "provider_name" else {}
    )
    assert unsafe not in str(error.model_dump())
    assert unsafe not in "".join(traceback.format_exception(excinfo.value))


def test_failed_submission_also_raises_normalized_error() -> None:
    payload = _payload(status="failed", data=None, error={"code": "FORBIDDEN"})
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_submission(payload)
    assert excinfo.value.error.code == "REMOTE_GENERATION_FAILED"
    assert excinfo.value.error.provider_code == "FORBIDDEN"


def test_failed_without_error_body_still_normalized() -> None:
    """`failed` без подробностей остаётся нормализованным отказом, без success."""
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(_payload(status="failed", data=None, error=None))
    assert excinfo.value.error.code == "REMOTE_GENERATION_FAILED"
    assert excinfo.value.error.provider_code is None
    assert excinfo.value.error.details == {}


def test_failed_ignores_non_allowlisted_metadata() -> None:
    """`metadata.raw` и прочие неизвестные ключи не попадают в details."""
    payload = _payload(
        status="failed",
        data=None,
        error={"code": "BAD_GATEWAY", "metadata": {"raw": "leak", "trace_id": "leak-trace"}},
    )
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)
    assert excinfo.value.error.details == {}


@pytest.mark.parametrize(
    "unsafe",
    [
        "file:///tmp/x",
        "javascript:alert(1)",
        "//cdn.example.invalid/x",
        "/relative/x",
        "https://user:pass@cdn.example.invalid/x",
        "https://cdn.example.invalid/a b",
        "https://cdn.example.invalid/a\nsecret-url-canary",
        "https:///x",
        "https://[invalid/x",
    ],
    ids=[
        "file",
        "javascript",
        "opaque",
        "relative",
        "userinfo",
        "space",
        "control",
        "missing-host",
        "bad-ipv6",
    ],
)
@pytest.mark.parametrize("shape", ["single", "list"])
def test_completed_rejects_unsafe_artifact_urls(unsafe: str, shape: str) -> None:
    """SYNTHETIC list form and documented single form both reject unsafe URLs."""
    data: object = {"url": unsafe} if shape == "single" else [{"url": IMAGE_URL}, {"url": unsafe}]
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload(data=data))
    assert excinfo.value.error.details == {"reason": "invalid_data"}
    assert unsafe not in str(excinfo.value.error.model_dump())
    assert unsafe not in "".join(traceback.format_exception(excinfo.value))


def test_text_only_completed_is_invalid_response() -> None:
    """Completed без image artifact (text-only) — безопасная ошибка, не success."""
    payload = _payload(data=None, content="Банан и яблоко — это фрукты.")
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(payload)
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "no_usable_image"}


def test_completed_without_data_is_invalid_response() -> None:
    payload = _payload()
    del payload["data"]
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_submission(payload)
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"


@pytest.mark.parametrize(
    "data",
    [
        {"url": ""},
        {"url": "   "},
        {"not_url": IMAGE_URL},
        "https://cdn.example.invalid/raw-string.jpg",
        42,
    ],
    ids=["empty-url", "blank-url", "no-url-key", "string-data", "number-data"],
)
def test_unrecognized_data_shape_fails_closed(data: object) -> None:
    payload = _payload(data=data)
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(payload)
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"
    assert excinfo.value.error.details == {"reason": "invalid_data"}


@pytest.mark.parametrize(
    "status",
    ["queued", "rendering", "unknown", "", "PENDING"],
    ids=["queued", "rendering", "unknown", "empty", "case"],
)
def test_unknown_status_is_invalid_response(status: str) -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(_payload(status=status, data=None))
    assert excinfo.value.error.details == {"reason": "invalid_status"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"object": "media.generation", "status": "pending"},
        {"id": "", "object": "media.generation", "status": "pending"},
        {"id": "   ", "object": "media.generation", "status": "pending"},
        {"id": 123, "object": "media.generation", "status": "pending"},
    ],
    ids=["empty", "missing-id", "blank-id", "spaces-id", "non-string-id"],
)
def test_missing_or_bad_id_is_invalid_response(payload: dict[str, object]) -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)
    assert excinfo.value.error.code == "PROVIDER_INVALID_RESPONSE"


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape",
        "a/b",
        "a?secret-id-canary",
        "a#fragment",
        ".",
        "..",
        "a%2Fb",
        "a\nsecret-id-canary",
        "a" * 129,
    ],
    ids=[
        "traversal",
        "slash",
        "query",
        "fragment",
        "dot",
        "dotdot",
        "encoded-slash",
        "control",
        "oversize",
    ],
)
def test_remote_id_must_be_bounded_single_segment(unsafe: str) -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_submission(_payload(id=unsafe, status="pending", data=None))
    assert excinfo.value.error.details == {"reason": "missing_id"}
    assert excinfo.value.error.provider_code is None
    assert excinfo.value.error.details == {"reason": "missing_id"}
    if len(unsafe) > 2:
        assert unsafe not in "".join(traceback.format_exception(excinfo.value))


@pytest.mark.parametrize(
    "safe_id", ["aig_synthetic", "gen_123", "550e8400-e29b-41d4-a716-446655440000"]
)
def test_safe_remote_ids_are_preserved(safe_id: str) -> None:
    submission = normalize_media_submission(_payload(id=safe_id, status="pending", data=None))
    assert submission.remote_ref is not None
    assert submission.remote_ref.remote_job_id == safe_id


@pytest.mark.parametrize(
    "object_value",
    [None, "image.generation", "media.generation.extra", 7],
    ids=["null", "wrong", "suffix", "number"],
)
def test_wrong_object_is_invalid_response(object_value: object) -> None:
    payload = _payload(object=object_value, data=None)
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)
    assert excinfo.value.error.details == {"reason": "invalid_object"}


def test_missing_status_is_invalid_response() -> None:
    payload = _payload()
    del payload["status"]
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)
    assert excinfo.value.error.details == {"reason": "invalid_status"}


@pytest.mark.parametrize(
    "payload",
    [None, "string", 42, ["list"]],
    ids=["null", "string", "number", "list"],
)
def test_non_object_payload_is_invalid_response(payload: object) -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(payload)
    assert excinfo.value.error.details == {"reason": "response_not_object"}


def test_result_requires_completed_status() -> None:
    """`fetch_result` не вызывается на pending; преждевременный вызов fail closed."""
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload(status="pending", data=None))
    assert excinfo.value.error.details == {"reason": "status_not_completed"}


def test_invalid_response_has_no_original_cause_or_context() -> None:
    with pytest.raises(ProviderError) as excinfo:
        normalize_media_status(_payload(status="bogus", data=None))
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_content_and_provider_metadata_are_preserved_safely() -> None:
    payload = _payload(content="synthetic text", warnings=["synthetic warning"])
    result = normalize_media_result(payload)
    assert result.content == "synthetic text"
    assert result.provider_metadata == {
        "model": SYNTHETIC_MODEL,
        "created": 1703001244,
        "warning_count": 1,
    }


@pytest.mark.parametrize("field", ["model", "warnings"])
@pytest.mark.parametrize(
    "canary",
    ["https://cdn.example.invalid/x?sig=secret-metadata-canary", "prompt secret-metadata-canary"],
    ids=["signed-url", "prompt"],
)
def test_provider_metadata_drops_provider_text_canaries(field: str, canary: str) -> None:
    payload = _payload(**{field: canary if field == "model" else [canary]})
    result = normalize_media_result(payload)
    assert canary not in str(result.provider_metadata)
    assert result.provider_metadata == (
        {"created": 1703001244}
        if field == "model"
        else {"model": SYNTHETIC_MODEL, "created": 1703001244, "warning_count": 1}
    )


@pytest.mark.parametrize("field", ["created", "completed_at"])
def test_timestamp_accepts_utc_calendar_bounds_and_rejects_oversize(field: str) -> None:
    # Local representability guard, not a provider or model-specific time limit.
    for boundary in (-62135596800, 253402300799):
        for value in (boundary, Decimal(boundary)):
            metadata = normalize_media_result(_payload(**{field: value})).provider_metadata
            assert metadata[field] == boundary
    for value in (-62135596801, 253402300800, Decimal("1e4300"), 10**4300):
        with pytest.raises(ProviderError) as excinfo:
            normalize_media_result(_payload(**{field: value}))
        assert excinfo.value.error.details == {"reason": "invalid_timestamp"}
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None


def test_timestamp_int_conversion_failure_has_no_raw_exception_context() -> None:
    canary = "secret-timestamp-conversion-canary"

    class FailingDecimal(Decimal):
        def __int__(self) -> int:
            raise ValueError(canary)

    with pytest.raises(ProviderError) as excinfo:
        normalize_media_result(_payload(created=FailingDecimal("1703001244")))
    assert excinfo.value.error.details == {"reason": "invalid_timestamp"}
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert canary not in "".join(traceback.format_exception(excinfo.value))


def test_response_mapper_imports_no_transport() -> None:
    """C09b — только чистый mapping ответа: без HTTP/сетевого транспорта."""
    tree = ast.parse(RESPONSE_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    roots = {name.split(".")[0] for name in imported}
    assert roots & {"httpx", "requests", "http", "socket", "aiohttp"} == set()
    assert not any(
        name == "urllib" or (name.startswith("urllib.") and name != "urllib.parse")
        for name in imported
    )


def test_fixtures_are_synthetic_not_documented_model_ids() -> None:
    """Схема документации — свидетельство формы, а не подтверждённый живой model ID."""
    for documented_example in ("google/gemini-2.5-flash-image", "google/veo3", "seedream-3"):
        assert SYNTHETIC_MODEL != documented_example
        assert REMOTE_ID != documented_example
