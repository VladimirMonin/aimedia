"""Типизированные доменные запросы.

Один универсальный request с десятками nullable-полей запрещён: каждый тип
задачи имеет собственную модель (`03-domain-model.md`, «Правильная модель
запросов»). В v0.1 существует `image.generate`; остальные kinds и их requests
добавляются вместе с соответствующими модулями, а не заранее.

`provider_options` — ограниченный escape hatch для специфических параметров
конкретной модели. Устойчивые доменные параметры имеют нормальные поля.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from aimedia.domain.base import DomainModel
from aimedia.domain.inputs import CompiledPrompt, InputRef
from aimedia.domain.refs import ModelRef, ProviderRef


class JobKind(StrEnum):
    """Тип AI-задачи.

    В v0.1 реально существует только `image.generate`; значения для будущих
    модулей не добавляются «на будущее» (`03-domain-model.md`).
    """

    IMAGE_GENERATE = "image.generate"


class FinalFormat(StrEnum):
    """Локальный конечный формат результата.

    Отделён от provider output: локальный WebP не означает native WebP модели,
    а alias `jpg` нормализуется в `jpeg` на границе CLI (решение baseline D08).
    """

    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class JobRequest(DomainModel):
    """Общая семантика всех доменных запросов.

    Наследование не является самоцелью: важен общий набор полей — `kind`,
    provider и логическая модель, по которым выбирается конкретный тип запроса.
    """

    kind: JobKind
    provider: ProviderRef
    model: ModelRef


class ImageGenerationRequest(JobRequest):
    """Запрос генерации изображения — основной тип v0.1.

    Допустимые значения `resolution`, `aspect_ratio` и `quality` определяет
    Model Registry: домен хранит запрошенное значение, а не список разрешённых
    (решение baseline D08).
    """

    kind: Literal[JobKind.IMAGE_GENERATE] = JobKind.IMAGE_GENERATE

    prompt: CompiledPrompt
    images: list[InputRef] = []

    aspect_ratio: str | None = None
    resolution: str | None = None
    quality: str | None = None

    # Финальный локальный формат (CLI `--format`) и, отдельно, параметр output
    # provider. Совпадать они не обязаны.
    final_format: FinalFormat | None = None
    output_format: str | None = None

    seed: int | None = None
    max_images: int = Field(default=1, ge=1)

    provider_options: dict[str, Any] = {}


AnyJobRequest = Annotated[ImageGenerationRequest, Field(discriminator="kind")]
