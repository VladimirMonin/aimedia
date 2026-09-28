"""C04-подготовка, собранная в C03-request и отправленная fake provider.

Этот тест доказывает стык двух срезов E02: подготовленные `PromptPreparation`
(`CompiledPrompt` + `PromptSource`) и `tuple[InputRef, ...]` складываются в
`ImageGenerationRequest`, который принимает fake provider через публичный порт
`ProviderGateway`. Соединение проверяется целиком, без сети: fake детерминирован,
а результат submit нормализован в доменные значения.

Ничего в этом тесте не подменяет production Polza adapter: fake живёт в
`tests/support` и не регистрируется в production registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path

import pytest
from fake_provider import FAKE_PROVIDER_ID, FakeImageProvider, FakeScenario
from image_fixtures import png_bytes, webp_lossless_bytes

from aimedia.application.inputs import ReferenceLimits, prepare_reference_images
from aimedia.application.prompts import PromptCompiler, file_source, inline_source
from aimedia.domain import (
    FinalFormat,
    ImageGenerationRequest,
    ModelRef,
    ProviderJobState,
    ProviderRef,
    ProviderResult,
    SubmissionResult,
)


def run(coro: Coroutine[object, object, object]) -> object:
    return asyncio.run(coro)


def _build_request(tmp_path: Path) -> ImageGenerationRequest:
    """Собрать request из подготовленных C04-данных, как это делает application."""
    (tmp_path / "base.md").write_text("Base character", encoding="utf-8")
    (tmp_path / "scene.md").write_text("Scene description", encoding="utf-8")
    first = tmp_path / "ref-1.png"
    first.write_bytes(png_bytes())
    second = tmp_path / "ref-2.webp"
    second.write_bytes(webp_lossless_bytes())

    prompt = PromptCompiler().compile(
        [
            file_source(tmp_path / "base.md"),
            inline_source("Instruction A"),
            file_source(tmp_path / "scene.md"),
        ]
    )
    images = prepare_reference_images([first, second])

    return ImageGenerationRequest(
        provider=ProviderRef(id=FAKE_PROVIDER_ID),
        model=ModelRef(id="fake-model"),
        prompt=prompt.compiled,
        images=list(images),
        final_format=FinalFormat.PNG,
    )


def test_prepared_inputs_form_a_submittable_request(tmp_path: Path) -> None:
    """Подготовленные prompt и references доходят до provider без потери данных."""
    request = _build_request(tmp_path)
    provider = FakeImageProvider()

    result = run(provider.submit(request))

    assert isinstance(result, SubmissionResult)
    assert result.state is ProviderJobState.COMPLETED
    assert provider.submit_count == 1

    submitted = provider.submitted_requests[0]
    assert submitted.prompt.text == "Base character\n\nInstruction A\n\nScene description"
    assert submitted.prompt.source_count == 3
    assert [image.mime_type for image in submitted.images] == ["image/png", "image/webp"]
    assert [image.position for image in submitted.images] == [0, 1]
    assert all(image.sha256 is not None for image in submitted.images)


def test_pending_provider_preserves_prepared_prompt_snapshot(tmp_path: Path) -> None:
    """Асинхронный submit сохраняет тот же snapshot, что был подготовлен."""
    request = _build_request(tmp_path)
    provider = FakeImageProvider(FakeScenario.PENDING)

    result = run(provider.submit(request))

    assert result.remote_ref is not None
    assert provider.submitted_requests[0].prompt == request.prompt


def test_empty_prompt_never_reaches_provider(tmp_path: Path) -> None:
    """Пустой prompt останавливается на подготовке: submit_count остаётся нулём."""
    from aimedia.domain import PromptRequiredError

    provider = FakeImageProvider()
    with pytest.raises(PromptRequiredError):
        PromptCompiler().compile([inline_source("   ")])

    assert provider.submit_count == 0


def test_invalid_reference_never_reaches_provider(tmp_path: Path) -> None:
    """Повреждённый reference останавливается до provider: submit_count == 0."""
    from aimedia.domain import UnsupportedInputFormatError

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    provider = FakeImageProvider()

    with pytest.raises(UnsupportedInputFormatError):
        prepare_reference_images([broken], limits=ReferenceLimits(max_references=1))

    assert provider.submit_count == 0


def test_fake_provider_result_is_a_normalized_domain_value(tmp_path: Path) -> None:
    """Нормализованный результат fake — доменное значение, а не сырой JSON."""
    request = _build_request(tmp_path)

    result = run(FakeImageProvider().submit(request))

    assert isinstance(result.result, ProviderResult)
    assert result.result.cost is not None
    assert result.result.remote_artifacts[0].content_type == "image/png"
