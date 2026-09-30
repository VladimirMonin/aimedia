"""Подготовка reference images одного Job: порядок, MIME, размер и SHA-256.

Контракт (`04-cli-contract.md`, «Правила `--image`»): каждый `--image` добавляет
один reference image, а порядок опций сохраняется — он значим для моделей с
несколькими референсами. Поэтому вход — упорядоченная последовательность путей, а
`InputRef.position` присваивается по факту порядка, а не берётся из внешнего поля.

Факты о входе берутся из его байтов, а не из расширения файла (D15, R05):

- MIME определяется по сигнатуре и структуре контейнера (`probe_image`);
- `size_bytes` — фактическая длина байтов;
- `sha256` — SHA-256 по этим же байтам, обязательный пункт истории (D15).

Ограничения типа и размера передаются вызывающей стороной. Локально подтверждён
только набор форматов `SUPPORTED_IMAGE_MIME_TYPES`; количественные лимиты модели
приходят из Model Registry (E03) и остаются `None`, пока неизвестны — выдуманное
«разумное» число здесь не подставляется (`06-model-registry.md`, «Input constraints
не должны выдумываться»).

Подготовка не пишет и не конвертирует файлы: локальная обработка изображений —
этап E05. Каждый вызов возвращает собственный неизменяемый кортеж, поэтому
reference images одного Job не смешиваются с batch-элементами соседнего.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aimedia.application.inputs.image_probe import (
    SUPPORTED_IMAGE_MIME_TYPES,
    InvalidImageContentError,
    probe_image,
)
from aimedia.domain.errors import (
    DomainError,
    InputFileNotFoundError,
    InvalidParameterValueError,
    TooManyReferenceImagesError,
    UnsupportedInputFormatError,
)
from aimedia.domain.inputs import InputKind, InputRef
from aimedia.logging import EventLogger


@dataclass(frozen=True, slots=True)
class ReferenceLimits:
    """Ограничения reference images, известные до submit.

    `max_references` и `max_size_bytes` приходят из Model Registry и остаются
    `None`, пока точное значение неизвестно: неизвестный лимит не превращается в
    придуманное число. `allowed_mime_types` по умолчанию равен локально
    подтверждённому набору форматов и может быть сужен provider-ограничением.
    """

    max_references: int | None = None
    max_size_bytes: int | None = None
    allowed_mime_types: frozenset[str] = field(default_factory=lambda: SUPPORTED_IMAGE_MIME_TYPES)

    def __post_init__(self) -> None:
        if self.max_references is not None and self.max_references < 0:
            raise ValueError("max_references не может быть отрицательным")
        if self.max_size_bytes is not None and self.max_size_bytes <= 0:
            raise ValueError("max_size_bytes должен быть положительным")


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    """Проверенный вход и неизменённые байты одного чтения; не доменный DTO."""

    ref: InputRef
    content: bytes = field(repr=False)


def prepare_reference_images(
    paths: Sequence[str | Path],
    *,
    limits: ReferenceLimits | None = None,
    logger: EventLogger | None = None,
) -> tuple[InputRef, ...]:
    """Подготовить reference images в порядке переданных путей.

    Возвращает новый кортеж `InputRef` с позицией, фактическим MIME, размером,
    SHA-256 и размерами изображения в `metadata`. Пустой список допустим: модель
    без reference images не является ошибкой (минимум 0 в Registry).
    """
    return tuple(
        snapshot.ref for snapshot in snapshot_reference_images(paths, limits=limits, logger=logger)
    )


def snapshot_reference_images(
    paths: Sequence[str | Path],
    *,
    limits: ReferenceLimits | None = None,
    logger: EventLogger | None = None,
) -> tuple[ReferenceSnapshot, ...]:
    """Один раз прочитать источники до создания Job; сохранить байты для CN-01."""
    effective = limits if limits is not None else ReferenceLimits()
    try:
        prepared = _prepare_all(paths, limits=effective)
    except DomainError as exc:
        _log_validation_failed(logger, exc)
        raise
    _log_input_prepared(logger, tuple(snapshot.ref for snapshot in prepared))
    return prepared


def validate_reference_content(ref: InputRef, content: bytes) -> None:
    """Сверить snapshot/копию по тем же probe, SHA-256, размеру и MIME, без IO."""
    probe = probe_image(content)
    if (
        ref.kind is not InputKind.IMAGE
        or ref.size_bytes != len(content)
        or ref.sha256 != hashlib.sha256(content).hexdigest()
        or ref.mime_type != probe.mime_type
    ):
        raise ValueError("Reference bytes do not match verified metadata")


def _prepare_all(
    paths: Sequence[str | Path], *, limits: ReferenceLimits
) -> tuple[ReferenceSnapshot, ...]:
    if limits.max_references is not None and len(paths) > limits.max_references:
        raise TooManyReferenceImagesError(
            f"Запрошено {len(paths)} reference images, допустимо не более {limits.max_references}.",
            details={"requested": len(paths), "max_references": limits.max_references},
        )
    return tuple(
        _prepare_one(path, position=position, limits=limits) for position, path in enumerate(paths)
    )


def _prepare_one(path: str | Path, *, position: int, limits: ReferenceLimits) -> ReferenceSnapshot:
    local_path = Path(path)
    details: dict[str, object] = {
        "path": local_path.as_posix(),
        "position": position,
        "parameter": "--image",
    }
    try:
        content = local_path.read_bytes()
    except OSError as exc:
        raise InputFileNotFoundError(
            f"Reference image {local_path} недоступна: {exc.strerror or exc}.",
            details=details,
        ) from exc

    size_bytes = len(content)
    if limits.max_size_bytes is not None and size_bytes > limits.max_size_bytes:
        raise InvalidParameterValueError(
            f"Reference image {local_path} имеет размер {size_bytes} байт, "
            f"допустимо не более {limits.max_size_bytes}.",
            details={
                **details,
                "size_bytes": size_bytes,
                "max_size_bytes": limits.max_size_bytes,
            },
        )

    try:
        probe = probe_image(content)
    except InvalidImageContentError as exc:
        raise UnsupportedInputFormatError(
            f"Reference image {local_path} не является корректным изображением: {exc.message}",
            details={**details, "extension": local_path.suffix.lower()},
        ) from exc

    if probe.mime_type not in limits.allowed_mime_types:
        raise UnsupportedInputFormatError(
            f"Формат {probe.mime_type} не принимается; допустимы "
            f"{', '.join(sorted(limits.allowed_mime_types))}.",
            details={
                **details,
                "detected_mime_type": probe.mime_type,
                "allowed_mime_types": sorted(limits.allowed_mime_types),
            },
        )

    return ReferenceSnapshot(
        ref=InputRef(
            kind=InputKind.IMAGE,
            path=local_path,
            position=position,
            mime_type=probe.mime_type,
            size_bytes=size_bytes,
            sha256=hashlib.sha256(content).hexdigest(),
            metadata={"width": probe.width, "height": probe.height},
        ),
        content=content,
    )


def _log_input_prepared(logger: EventLogger | None, prepared: Sequence[InputRef]) -> None:
    """Записать подготовку входов: числа и размеры, без содержимого файлов.

    Полные пути и метаданные картинок в диагностику не попадают: контракт E02
    разрешает числа источников и размеры, а не содержимое входов.
    """
    if logger is None:
        return
    logger.event(
        "input_prepared",
        details={
            "reference_count": len(prepared),
            "total_size_bytes": sum(ref.size_bytes or 0 for ref in prepared),
            "mime_types": sorted({ref.mime_type for ref in prepared if ref.mime_type}),
        },
    )


def _log_validation_failed(logger: EventLogger | None, exc: DomainError) -> None:
    """Записать отказ подготовки до submit без содержимого входных файлов."""
    if logger is None:
        return
    logger.event(
        "validation_failed",
        level="WARNING",
        details={"code": exc.code.value},
    )
