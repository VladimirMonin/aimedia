"""Подготовка reference images: повреждённый файл, порядок, hash, MIME, snapshot.

Главное утверждение (`docs/plans/README.md`, E02): повреждённое изображение не
принимается по одному расширению, а порядок references и их hashes сохраняются.
Дополнительно проверяется, что MIME определяется по фактическим байтам, что
изменение исходного файла после подготовки не меняет подготовленные данные, и что
явный лимит типа/размера отклоняет вход до submit.

Синтетические байты собирает `tests/support/image_fixtures.py`: каждый
`*_corrupted`-вариант ломает ровно одну проверяемую инварианту, поэтому падение
доказывает конкретную проверку, а не совпадение.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest
from image_fixtures import (
    interlaced_png_bytes,
    jpeg_bytes,
    jpeg_with_filler_bytes,
    jpeg_with_invalid_segment_length,
    jpeg_with_segment_beyond_file,
    jpeg_with_short_sof,
    jpeg_with_truncated_segment_header,
    jpeg_with_zero_dimensions,
    jpeg_without_eoi,
    jpeg_without_sof,
    jpeg_without_start_marker,
    png_bytes,
    png_chunk,
    png_with_bad_crc,
    png_with_chunk_beyond_file,
    png_with_duplicate_ihdr,
    png_with_invalid_ihdr,
    png_with_non_alpha_chunk_name,
    png_with_short_idat,
    png_with_truncated_chunk_header,
    png_with_undecodable_idat,
    png_without_idat,
    png_without_iend,
    png_without_ihdr_first,
    truncated_png,
    webp_extended_bytes,
    webp_header_without_chunks,
    webp_lossless_bytes,
    webp_lossy_bytes,
    webp_short_vp8_header,
    webp_truncated_header,
    webp_with_bad_vp8l_header,
    webp_with_chunk_beyond_file,
    webp_with_short_vp8x,
    webp_with_truncated_chunk_header,
    webp_with_wrong_riff_size,
    webp_without_frame,
)

from aimedia.application.inputs import (
    ReferenceLimits,
    prepare_reference_images,
    probe_image,
)
from aimedia.application.inputs.image_probe import SUPPORTED_IMAGE_MIME_TYPES
from aimedia.domain import (
    DomainErrorCode,
    InputFileNotFoundError,
    InvalidParameterValueError,
    TooManyReferenceImagesError,
    UnsupportedInputFormatError,
)


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _rejects(tmp_path: Path, name: str, content: bytes) -> None:
    """Записать байты как reference и потребовать явного отказа до submit."""
    broken = _write(tmp_path, name, content)
    with pytest.raises(UnsupportedInputFormatError):
        prepare_reference_images([broken])


# --- Фактический формат важнее расширения -----------------------------------


def test_png_extension_does_not_make_text_an_image(tmp_path: Path) -> None:
    """Не-изображение с расширением `.png` не принимается по расширению."""
    fake = _write(tmp_path, "photo.png", b"this is not an image at all")

    with pytest.raises(UnsupportedInputFormatError) as exc_info:
        prepare_reference_images([fake])

    assert exc_info.value.code is DomainErrorCode.UNSUPPORTED_INPUT_FORMAT
    assert exc_info.value.details["path"].endswith("photo.png")


def test_corrupted_png_with_valid_signature_is_rejected(tmp_path: Path) -> None:
    """PNG с корректной сигнатурой, но повреждённым CRC не проходит."""
    _rejects(tmp_path, "broken.png", png_with_bad_crc())


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("truncated.png", truncated_png()),
        ("no-iend.png", png_without_iend()),
        ("sig-only.png", png_bytes()[:8]),
        ("cut-header.png", png_with_truncated_chunk_header()),
        ("bad-name.png", png_with_non_alpha_chunk_name()),
        ("out-of-bounds.png", png_with_chunk_beyond_file()),
        ("no-ihdr-first.png", png_without_ihdr_first()),
        ("double-ihdr.png", png_with_duplicate_ihdr()),
        ("no-idat.png", png_without_idat()),
        ("short-idat.png", png_with_short_idat()),
        ("bad-idat.png", png_with_undecodable_idat()),
        ("trailing.png", png_bytes() + b"junk"),
    ],
)
def test_corrupted_png_content_is_rejected(tmp_path: Path, name: str, content: bytes) -> None:
    """Каждый вид повреждения PNG отклоняется до provider submit."""
    _rejects(tmp_path, name, content)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("short.jpg", b"\xff\xd8\xff\xd9"),
        ("no-eoi.jpg", jpeg_without_eoi()),
        ("no-sof.jpeg", jpeg_without_sof()),
        ("zero.jpg", jpeg_with_zero_dimensions()),
        ("short-segment.jpg", jpeg_with_invalid_segment_length()),
        ("no-marker.jpg", jpeg_without_start_marker()),
        ("truncated-seg.jpg", jpeg_with_truncated_segment_header()),
        ("beyond.jpg", jpeg_with_segment_beyond_file()),
        ("short-sof.jpg", jpeg_with_short_sof()),
    ],
)
def test_corrupted_jpeg_content_is_rejected(tmp_path: Path, name: str, content: bytes) -> None:
    """Каждый вид повреждения JPEG отклоняется до provider submit."""
    _rejects(tmp_path, name, content)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("wrong-riff.webp", webp_with_wrong_riff_size()),
        ("no-frame.webp", webp_without_frame()),
        ("short-header.webp", webp_truncated_header()),
        ("empty.webp", webp_header_without_chunks()),
        ("truncated-chunk.webp", webp_with_truncated_chunk_header()),
        ("beyond.webp", webp_with_chunk_beyond_file()),
        ("short-vp8x.webp", webp_with_short_vp8x()),
        ("bad-vp8l.webp", webp_with_bad_vp8l_header()),
        ("short-vp8.webp", webp_short_vp8_header()),
    ],
)
def test_corrupted_webp_content_is_rejected(tmp_path: Path, name: str, content: bytes) -> None:
    """Каждый вид повреждения WebP отклоняется до provider submit."""
    _rejects(tmp_path, name, content)


@pytest.mark.parametrize(
    "ihdr",
    [
        struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)[:12],  # короткий IHDR
        struct.pack(">IIBBBBB", 0, 2, 8, 2, 0, 0, 0),  # нулевая ширина
        struct.pack(">IIBBBBB", 2, 0, 8, 2, 0, 0, 0),  # нулевая высота
        struct.pack(">IIBBBBB", 2, 2, 3, 2, 0, 0, 0),  # недопустимая битовая глубина
        struct.pack(">IIBBBBB", 2, 2, 8, 5, 0, 0, 0),  # неизвестный color type
        struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 7),  # неизвестная развёртка
    ],
    ids=["short", "zero-width", "zero-height", "bad-depth", "bad-color", "bad-interlace"],
)
def test_png_with_invalid_ihdr_is_rejected(tmp_path: Path, ihdr: bytes) -> None:
    _rejects(tmp_path, "bad-ihdr.png", png_with_invalid_ihdr(ihdr))


def test_mime_is_detected_from_content_not_extension(tmp_path: Path) -> None:
    """MIME берётся из байтов: `*.png` с JPEG-содержимым определяется как JPEG."""
    disguised = _write(tmp_path, "actually-jpeg.png", jpeg_bytes())

    prepared = prepare_reference_images([disguised])

    assert prepared[0].mime_type == "image/jpeg"


def test_probe_reports_dimensions_and_mime() -> None:
    probe = probe_image(png_bytes(width=7, height=11))

    assert probe.mime_type == "image/png"
    assert (probe.width, probe.height) == (7, 11)


# --- Подтверждённые форматы принимаются -------------------------------------


def test_extended_webp_frame_is_accepted(tmp_path: Path) -> None:
    """Расширенный WebP (VP8X) принимается и отдаёт размеры кадра."""
    path = _write(tmp_path, "extended.webp", webp_extended_bytes(width=3, height=5))

    prepared = prepare_reference_images([path])

    assert prepared[0].mime_type == "image/webp"
    assert prepared[0].metadata == {"width": 3, "height": 5}


def test_lossy_webp_frame_is_accepted(tmp_path: Path) -> None:
    """Lossy-WebP (VP8) принимается и отдаёт размеры кадра."""
    path = _write(tmp_path, "lossy.webp", webp_lossy_bytes(width=4, height=6))

    prepared = prepare_reference_images([path])

    assert prepared[0].metadata == {"width": 4, "height": 6}


def test_interlaced_png_is_accepted(tmp_path: Path) -> None:
    """Adam7-развёртка (interlace=1) не считается повреждением."""
    path = _write(tmp_path, "interlaced.png", interlaced_png_bytes(width=3, height=4))

    prepared = prepare_reference_images([path])

    assert prepared[0].mime_type == "image/png"
    assert prepared[0].metadata == {"width": 3, "height": 4}


def test_jpeg_with_filler_and_restart_markers_is_accepted(tmp_path: Path) -> None:
    """0xFF-заполнители и restart-маркеры не ломают разбор JPEG."""
    path = _write(tmp_path, "fillers.jpg", jpeg_with_filler_bytes())

    prepared = prepare_reference_images([path])

    assert prepared[0].mime_type == "image/jpeg"
    assert prepared[0].metadata == {"width": 2, "height": 2}


def test_multiple_idat_chunks_are_joined(tmp_path: Path) -> None:
    """Разбитый на несколько IDAT PNG собирается обратно корректно."""
    raw = b"\x00" + bytes(6) + b"\x00" + bytes(6)
    import zlib

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    content = (
        png_bytes()[:8]
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw)[:4])
        + png_chunk(b"IDAT", zlib.compress(raw)[4:])
        + png_chunk(b"IEND", b"")
    )
    path = _write(tmp_path, "split-idat.png", content)

    prepared = prepare_reference_images([path])

    assert prepared[0].metadata == {"width": 2, "height": 2}


# --- Порядок, размер и hash --------------------------------------------------


def test_reference_order_and_positions_are_preserved(tmp_path: Path) -> None:
    """Порядок `--image` сохраняется и выражается в `InputRef.position`."""
    first = _write(tmp_path, "refs-1.png", png_bytes())
    second = _write(tmp_path, "refs-2.jpg", jpeg_bytes())
    third = _write(tmp_path, "refs-3.webp", webp_lossless_bytes())

    prepared = prepare_reference_images([first, second, third])

    assert [ref.path for ref in prepared] == [first, second, third]
    assert [ref.position for ref in prepared] == [0, 1, 2]
    assert [ref.mime_type for ref in prepared] == ["image/png", "image/jpeg", "image/webp"]


def test_size_and_sha256_match_real_bytes(tmp_path: Path) -> None:
    """Размер — фактическая длина файла, SHA-256 — по тем же байтам."""
    content = png_bytes(width=3, height=3)
    path = _write(tmp_path, "hash.png", content)

    prepared = prepare_reference_images([path])

    ref = prepared[0]
    assert ref.size_bytes == len(content)
    assert ref.sha256 == hashlib.sha256(content).hexdigest()


def test_metadata_carries_dimensions(tmp_path: Path) -> None:
    path = _write(tmp_path, "dims.png", png_bytes(width=5, height=9))

    prepared = prepare_reference_images([path])

    assert prepared[0].metadata == {"width": 5, "height": 9}


def test_empty_reference_list_is_allowed() -> None:
    """Модель без reference images не является ошибкой (минимум 0)."""
    assert prepare_reference_images([]) == ()


def test_source_mutation_after_preparation_keeps_hash_and_mime(tmp_path: Path) -> None:
    """Изменение исходного файла после подготовки не меняет подготовленные данные."""
    path = _write(tmp_path, "ref.png", png_bytes())
    prepared = prepare_reference_images([path])
    ref = prepared[0]

    path.write_bytes(jpeg_bytes())

    assert ref.mime_type == "image/png"
    assert ref.sha256 == hashlib.sha256(png_bytes()).hexdigest()
    assert ref.size_bytes == len(png_bytes())
    # Новая подготовка читает актуальный файл: подготовка не является кэшем.
    assert prepare_reference_images([path])[0].mime_type == "image/jpeg"


def test_missing_reference_file_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "absent.png"

    with pytest.raises(InputFileNotFoundError) as exc_info:
        prepare_reference_images([missing])

    assert exc_info.value.code is DomainErrorCode.INPUT_FILE_NOT_FOUND
    assert exc_info.value.details["parameter"] == "--image"


def test_directory_as_reference_is_reported(tmp_path: Path) -> None:
    """Каталог вместо файла даёт понятную ошибку, а не сырой трейсбек."""
    with pytest.raises(InputFileNotFoundError):
        prepare_reference_images([tmp_path])


# --- Ограничения типа и размера (без выдуманных model-specific лимитов) ------


def test_default_limits_do_not_invent_numbers() -> None:
    """Неизвестные количественные лимиты остаются `None`, а не «разумным» числом."""
    limits = ReferenceLimits()

    assert limits.max_references is None
    assert limits.max_size_bytes is None
    assert limits.allowed_mime_types == SUPPORTED_IMAGE_MIME_TYPES


def test_reference_count_limit_is_enforced(tmp_path: Path) -> None:
    paths = [_write(tmp_path, f"ref-{index}.png", png_bytes()) for index in range(3)]

    with pytest.raises(TooManyReferenceImagesError) as exc_info:
        prepare_reference_images(paths, limits=ReferenceLimits(max_references=2))

    assert exc_info.value.code is DomainErrorCode.TOO_MANY_REFERENCE_IMAGES
    assert exc_info.value.details == {"requested": 3, "max_references": 2}


def test_reference_count_within_limit_passes(tmp_path: Path) -> None:
    paths = [_write(tmp_path, "a.png", png_bytes()), _write(tmp_path, "b.png", png_bytes())]

    prepared = prepare_reference_images(paths, limits=ReferenceLimits(max_references=2))

    assert len(prepared) == 2


def test_reference_size_limit_is_enforced(tmp_path: Path) -> None:
    content = png_bytes(width=32, height=32)
    path = _write(tmp_path, "big.png", content)

    with pytest.raises(InvalidParameterValueError) as exc_info:
        prepare_reference_images([path], limits=ReferenceLimits(max_size_bytes=len(content) - 1))

    assert exc_info.value.code is DomainErrorCode.INVALID_PARAMETER_VALUE
    assert exc_info.value.details["size_bytes"] == len(content)


def test_confirmed_type_restriction_rejects_other_format(tmp_path: Path) -> None:
    """Явно подтверждённый набор типов действительно ограничивает вход."""
    path = _write(tmp_path, "photo.jpg", jpeg_bytes())

    with pytest.raises(UnsupportedInputFormatError) as exc_info:
        prepare_reference_images(
            [path], limits=ReferenceLimits(allowed_mime_types=frozenset({"image/png"}))
        )

    assert exc_info.value.details["detected_mime_type"] == "image/jpeg"


def test_negative_limits_are_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="отрицательным"):
        ReferenceLimits(max_references=-1)
    with pytest.raises(ValueError, match="положительным"):
        ReferenceLimits(max_size_bytes=0)


# --- Изоляция batch-элементов ------------------------------------------------


def test_batch_items_do_not_share_reference_state(tmp_path: Path) -> None:
    """Reference images разных Jobs не смешиваются между вызовами."""
    first = _write(tmp_path, "job-1.png", png_bytes())
    second = _write(tmp_path, "job-2.webp", webp_lossless_bytes())

    job_one = prepare_reference_images([first])
    job_two = prepare_reference_images([second])

    assert [ref.path for ref in job_one] == [first]
    assert [ref.path for ref in job_two] == [second]
    assert [ref.position for ref in job_one] == [0]
    assert [ref.position for ref in job_two] == [0]
