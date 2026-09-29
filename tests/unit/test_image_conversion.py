"""Локальная конвертация изображений: формат, alpha, размеры и отказ на мусоре.

Изображения собираются реальным Pillow, а не синтетическими байтами: тест
проверяет фактическое поведение декодера и кодера, которыми пользуется
production-конвертер. Каждый negative-кейс ломает ровно одну проверяемую
инварианту (концевой маркер, CRC, сигнатуру, лимит), поэтому падение указывает на
конкретную проверку.

Главные утверждения этапа E05 (`docs/plans/README.md`): формат определяется
декодированием байтов, а не расширением; PNG/WebP сохраняют alpha; JPEG получает
документированный белый фон; скрытого изменения размеров нет; фактические
`mime_type`/размеры берутся из конечных байтов.
"""

from __future__ import annotations

import struct
import traceback
import zlib
from io import BytesIO

import pytest
from PIL import Image, UnidentifiedImageError

from aimedia.artifacts.image import (
    PNG_MIME_TYPE,
    WEBP_MIME_TYPE,
    ImageConversionError,
    ImageDecodeError,
    ImageTooLargeError,
    UnsupportedImageInputError,
    UnsupportedImageModeError,
    convert_image,
)
from aimedia.domain.requests import FinalFormat

_WHITE = (255, 255, 255)


def _encode(image: Image.Image, pillow_format: str, **options: object) -> bytes:
    buffer = BytesIO()
    image.save(buffer, pillow_format, **options)
    return buffer.getvalue()


def _rgba_png(width: int = 4, height: int = 3) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    image.putdata(
        [
            (x * 40 % 256, y * 60 % 256, (x + y) * 30 % 256, 255 - (x * 10 % 200))
            for y in range(height)
            for x in range(width)
        ]
    )
    return image


def _decoded(image_bytes: bytes) -> Image.Image:
    image = Image.open(BytesIO(image_bytes))
    image.load()
    return image


def test_rgba_png_to_webp_keeps_alpha_pixels_and_dimensions():
    source = _rgba_png()

    result = convert_image(_encode(source, "PNG"), final_format=FinalFormat.WEBP)

    assert result.converted is True
    assert result.source_format is FinalFormat.PNG
    assert result.final_format is FinalFormat.WEBP
    assert result.mime_type == WEBP_MIME_TYPE
    assert (result.width, result.height) == source.size
    final = _decoded(result.data)
    assert final.format == "WEBP"
    assert (final.width, final.height) == source.size
    assert "A" in final.getbands()
    assert final.convert("RGBA").tobytes() == source.tobytes()
    assert result.alpha_flattened is False


def test_webp_result_converts_back_to_png_with_identical_pixels():
    source = _rgba_png(5, 4)

    to_webp = convert_image(_encode(source, "PNG"), final_format=FinalFormat.WEBP)
    back_to_png = convert_image(to_webp.data, final_format=FinalFormat.PNG)

    assert back_to_png.source_format is FinalFormat.WEBP
    assert back_to_png.final_format is FinalFormat.PNG
    assert back_to_png.mime_type == PNG_MIME_TYPE
    assert (back_to_png.width, back_to_png.height) == source.size
    returned = _decoded(back_to_png.data)
    assert returned.format == "PNG"
    assert returned.convert("RGBA").tobytes() == source.tobytes()


def test_rgba_to_jpeg_flattens_on_white_and_signals_alpha_loss():
    rgba = convert_image(_encode(_rgba_png(), "PNG"), final_format=FinalFormat.JPEG)
    transparent_pixel_png = Image.new("RGBA", (2, 1), (10, 20, 30, 0))

    result = convert_image(_encode(transparent_pixel_png, "PNG"), final_format=FinalFormat.JPEG)

    assert result.alpha_flattened is True
    assert result.mime_type == "image/jpeg"
    final = _decoded(result.data)
    assert final.format == "JPEG"
    assert "A" not in final.getbands()
    # Полностью прозрачный пиксель становится белым фоном, а не чёрным.
    assert final.getpixel((0, 0)) == _WHITE
    assert (result.width, result.height) == (2, 1)
    assert rgba.alpha_flattened is True


def test_palette_transparency_to_jpeg_flattens_on_white():
    palette = Image.new("P", (2, 1), 1)
    palette.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    source = _encode(palette, "PNG", transparency=1)

    result = convert_image(source, final_format=FinalFormat.JPEG)

    assert result.source_format is FinalFormat.PNG
    assert result.alpha_flattened is True
    assert _decoded(result.data).getpixel((0, 0)) == _WHITE


def test_palette_transparency_to_webp_keeps_transparency():
    palette = Image.new("P", (2, 1), 1)
    palette.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    source = _encode(palette, "PNG", transparency=1)

    result = convert_image(source, final_format=FinalFormat.WEBP)

    assert result.mime_type == WEBP_MIME_TYPE
    assert result.alpha_flattened is False
    final = _decoded(result.data)
    assert "A" in final.getbands()
    assert final.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize(
    ("mode", "color", "transparent"),
    [
        ("RGB", (12, 34, 56), (12, 34, 56)),
        ("L", 0, 0),
    ],
)
def test_png_trns_to_webp_and_jpeg_preserves_or_flattens_alpha(
    mode: str, color: tuple[int, int, int] | int, transparent: tuple[int, int, int] | int
):
    source = Image.new(mode, (2, 1), color)
    png = _encode(source, "PNG", transparency=transparent)
    assert _decoded(png).info["transparency"] == transparent

    webp = convert_image(png, final_format=FinalFormat.WEBP)
    decoded_webp = _decoded(webp.data)
    assert "A" in decoded_webp.getbands()
    assert decoded_webp.getpixel((0, 0))[3] == 0
    assert webp.alpha_flattened is False

    jpeg = convert_image(png, final_format=FinalFormat.JPEG)
    assert jpeg.alpha_flattened is True
    assert _decoded(jpeg.data).getpixel((0, 0)) == _WHITE


def test_grayscale_alpha_png_to_webp_keeps_alpha_band():
    source = _encode(Image.new("LA", (3, 2), (5, 128)), "PNG")

    result = convert_image(source, final_format=FinalFormat.WEBP)

    final = _decoded(result.data)
    assert "A" in final.getbands()
    assert final.convert("RGBA").getpixel((0, 0)) == (5, 5, 5, 128)


def test_jpeg_to_png_keeps_dimensions_and_reports_decoded_metadata():
    source = _encode(Image.new("RGB", (7, 5), (12, 34, 56)), "JPEG", quality=95)

    result = convert_image(source, final_format=FinalFormat.PNG)

    assert result.final_format is FinalFormat.PNG
    assert result.mime_type == PNG_MIME_TYPE
    assert (result.width, result.height) == (7, 5)
    final = _decoded(result.data)
    assert final.format == "PNG"
    assert final.size == (7, 5)


def test_same_format_keeps_valid_bytes_without_recompression():
    source = _encode(_rgba_png(), "PNG")

    result = convert_image(source, final_format=FinalFormat.PNG)

    assert result.converted is False
    assert result.data == source
    assert result.mime_type == PNG_MIME_TYPE
    assert result.source_format is FinalFormat.PNG
    assert result.alpha_flattened is False


def test_same_format_jpeg_keeps_original_lossy_bytes():
    source = _encode(Image.new("RGB", (6, 4), (9, 8, 7)), "JPEG", quality=30)

    result = convert_image(source, final_format=FinalFormat.JPEG)

    assert result.converted is False
    assert result.data == source
    assert result.mime_type == "image/jpeg"


@pytest.mark.parametrize(
    ("image_format", "final_format", "expected_mime", "decoded_format"),
    [
        ("PNG", FinalFormat.JPEG, "image/jpeg", "JPEG"),
        ("PNG", FinalFormat.WEBP, WEBP_MIME_TYPE, "WEBP"),
        ("JPEG", FinalFormat.PNG, PNG_MIME_TYPE, "PNG"),
        ("JPEG", FinalFormat.WEBP, WEBP_MIME_TYPE, "WEBP"),
        ("WEBP", FinalFormat.PNG, PNG_MIME_TYPE, "PNG"),
        ("WEBP", FinalFormat.JPEG, "image/jpeg", "JPEG"),
    ],
)
def test_reported_format_and_mime_match_decoded_final_bytes(
    image_format: str, final_format: FinalFormat, expected_mime: str, decoded_format: str
):
    source = _encode(Image.new("RGB", (5, 3), (200, 100, 50)), image_format)

    result = convert_image(source, final_format=final_format)

    final = _decoded(result.data)
    assert final.format == decoded_format
    assert result.mime_type == expected_mime
    assert final.get_format_mimetype() == expected_mime
    assert result.converted is True


def test_content_decides_format_not_an_imagined_extension():
    """Те же байты дают один и тот же разбор независимо от «ожидаемого» имени."""
    gif_bytes = _encode(Image.new("P", (2, 2)), "GIF")

    with pytest.raises(UnsupportedImageInputError):
        convert_image(gif_bytes, final_format=FinalFormat.PNG)

    spoofed = b"\x89PNG\r\n\x1a\n" + gif_bytes
    with pytest.raises((UnsupportedImageInputError, ImageDecodeError)):
        convert_image(spoofed, final_format=FinalFormat.WEBP)


def test_truncated_input_is_rejected():
    png = _encode(_rgba_png(), "PNG")
    jpeg = _encode(Image.new("RGB", (4, 3), (1, 2, 3)), "JPEG", quality=80)
    webp = _encode(_rgba_png(), "WEBP", lossless=True)

    payloads = {
        "png_half": png[: len(png) // 2],
        "png_last_byte": png[:-1],
        "png_missing_iend": png[: png.rfind(b"IEND")],
        "jpeg_half": jpeg[: len(jpeg) // 2],
        "jpeg_last_byte": jpeg[:-1],
        "jpeg_missing_eoi": jpeg[: jpeg.rfind(b"\xff\xd9")],
        "webp_half": webp[: len(webp) // 2],
    }

    for payload in payloads.values():
        with pytest.raises(ImageDecodeError):
            convert_image(payload, final_format=FinalFormat.WEBP)


def test_corrupt_png_payload_fails_full_decode():
    payload = bytearray(_encode(_rgba_png(), "PNG"))
    idat_start = payload.find(b"IDAT") + 4
    payload[idat_start] ^= 0xFF

    with pytest.raises((ImageDecodeError, UnsupportedImageInputError)):
        convert_image(bytes(payload), final_format=FinalFormat.WEBP)


def test_unsupported_or_empty_input_is_rejected():
    for payload in (
        b"",
        b"plain text pretending to be an image",
        b"\xff\xd8",  # SOI без сегментов
        b"RIFF\x00\x00\x00\x00WEBP",
    ):
        with pytest.raises((UnsupportedImageInputError, ImageDecodeError)):
            convert_image(payload, final_format=FinalFormat.PNG)


def test_too_large_payload_is_rejected_before_decode():
    source = _encode(_rgba_png(), "PNG")

    with pytest.raises(ImageTooLargeError):
        convert_image(source, final_format=FinalFormat.WEBP, max_bytes=16)


def test_too_many_pixels_is_rejected():
    source = _encode(Image.new("RGB", (20, 20), (1, 2, 3)), "PNG")

    with pytest.raises(ImageTooLargeError):
        convert_image(source, final_format=FinalFormat.WEBP, max_pixels=100)


def _png_header_only(width: int, height: int) -> bytes:
    """PNG без raster-данных: декодер читает IHDR и применяет свой порог пикселей."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b""))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _gray16_png(values: list[int]) -> bytes:
    """16-битный grayscale PNG: реальная градация, которую 8-битный контейнер не хранит."""
    raw = b"\x00" + b"".join(struct.pack(">H", value) for value in values)
    ihdr = struct.pack(">IIBBBBB", len(values), 1, 16, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def test_decompression_bomb_warning_becomes_typed_error():
    # 12000 × 12000 = 144 Мп: больше порога Pillow (89M) и меньше жёсткого отказа.
    payload = _png_header_only(12000, 12000)

    with pytest.raises(ImageTooLargeError):
        convert_image(payload, final_format=FinalFormat.PNG, max_pixels=10**12)


def test_decompression_bomb_hard_limit_becomes_typed_error():
    payload = _png_header_only(30000, 30000)

    with pytest.raises(ImageTooLargeError):
        convert_image(payload, final_format=FinalFormat.WEBP, max_pixels=10**12)


@pytest.mark.parametrize(("max_bytes", "max_pixels"), [(0, 10), (-1, 10), (10, 0), (10, -5)])
def test_non_positive_limits_are_rejected(max_bytes: int, max_pixels: int):
    with pytest.raises(ValueError, match="положительными"):
        convert_image(
            _encode(_rgba_png(), "PNG"),
            final_format=FinalFormat.PNG,
            max_bytes=max_bytes,
            max_pixels=max_pixels,
        )


def test_palette_png_without_transparency_to_jpeg_uses_rgb_palette_colors():
    palette = Image.new("P", (2, 1), 0)
    palette.putpalette([200, 100, 50] + [0] * 765)
    source = _encode(palette, "PNG")

    result = convert_image(source, final_format=FinalFormat.JPEG)

    assert result.alpha_flattened is False
    assert _decoded(result.data).getpixel((0, 0)) == (200, 100, 50)


def test_jpeg_with_trailing_bytes_is_rejected_despite_decodable_scan():
    source = _encode(Image.new("RGB", (4, 3), (1, 2, 3)), "JPEG", quality=80) + b"junk"

    with pytest.raises(ImageDecodeError):
        convert_image(source, final_format=FinalFormat.PNG)


def test_webp_with_mismatched_riff_size_is_rejected():
    source = _encode(_rgba_png(), "WEBP", lossless=True) + b"\x00\x00\x00\x00"

    with pytest.raises(ImageDecodeError):
        convert_image(source, final_format=FinalFormat.PNG)


def test_sixteen_bit_gray_to_jpeg_is_refused_instead_of_clamped():
    source = _gray16_png([0x0000, 0x0102, 0x7FFF, 0xFFFF])

    with pytest.raises(UnsupportedImageModeError):
        convert_image(source, final_format=FinalFormat.JPEG)


def test_unsupported_container_is_rejected_by_signature():
    tiff = _encode(Image.new("RGB", (3, 2), (1, 2, 3)), "TIFF")

    with pytest.raises(UnsupportedImageInputError):
        convert_image(tiff, final_format=FinalFormat.PNG)


def test_error_messages_do_not_embed_payload_or_paths():
    marker = b"SECRET_PAYLOAD_MARKER"
    payload = marker + b" not an image at all"

    with pytest.raises(UnsupportedImageInputError) as excinfo:
        convert_image(payload, final_format=FinalFormat.PNG)

    message = str(excinfo.value)
    assert "SECRET_PAYLOAD_MARKER" not in message
    assert "\\" not in message and "/" not in message


@pytest.mark.parametrize(
    ("stage", "pillow_error", "expected_error"),
    [
        ("header", UnidentifiedImageError, UnsupportedImageInputError),
        ("header", Image.DecompressionBombWarning, ImageTooLargeError),
        ("header", OSError, ImageDecodeError),
        ("verify", OSError, ImageDecodeError),
        ("load", OSError, ImageDecodeError),
        ("load", Image.DecompressionBombError, ImageTooLargeError),
        ("encode", OSError, ImageConversionError),
        ("encode", ValueError, ImageConversionError),
        ("final", OSError, ImageConversionError),
        ("final", Image.DecompressionBombError, ImageConversionError),
        ("final_load", OSError, ImageConversionError),
    ],
)
def test_pillow_failure_discards_untrusted_exception_context(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    pillow_error: type[Exception],
    expected_error: type[ImageConversionError],
):
    canary = "PRIVATE_DECODER_CANARY"
    original_open = Image.open
    original_save = Image.Image.save
    opens = 0

    def fail(*args: object, **kwargs: object) -> None:
        raise pillow_error(canary)

    def patched_open(*args: object, **kwargs: object) -> Image.Image:
        nonlocal opens
        opens += 1
        if stage == "header" or (stage == "final" and opens == 3):
            fail()
        image = original_open(*args, **kwargs)
        if stage == "verify" and opens == 1:
            monkeypatch.setattr(image, "verify", fail)
        if (stage == "load" and opens == 2) or (stage == "final_load" and opens == 3):
            monkeypatch.setattr(image, "load", fail)
        return image

    def patched_save(image: Image.Image, *args: object, **kwargs: object) -> None:
        if stage == "encode":
            fail()
        original_save(image, *args, **kwargs)

    monkeypatch.setattr(Image, "open", patched_open)
    monkeypatch.setattr(Image.Image, "save", patched_save)
    # Сборка входных байтов через оригинальный save не зависит от подмены Pillow.
    buffer = BytesIO()
    original_save(Image.new("RGB", (2, 1)), buffer, "PNG")
    source = buffer.getvalue()

    with pytest.raises(expected_error) as excinfo:
        convert_image(source, final_format=FinalFormat.WEBP)

    error = excinfo.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert canary not in str(error)
    assert canary not in "".join(traceback.format_exception(error))


def test_conversion_does_not_write_files(tmp_path):
    before = sorted(tmp_path.iterdir())

    result = convert_image(
        _encode(_rgba_png(), "PNG"),
        final_format=FinalFormat.WEBP,
    )

    assert result.data
    assert sorted(tmp_path.iterdir()) == before
    assert isinstance(result.data, bytes)


def test_sixteen_bit_gray_to_webp_is_refused_instead_of_clamped():
    # 16-битная градация серого в 8-битном WebP потеряла бы полутона: кодек не
    # заявляет успешную конвертацию, а возвращает типизированную ошибку.
    source = _gray16_png([0x0000, 0x0102, 0x7FFF, 0xFFFF])

    with pytest.raises(UnsupportedImageModeError):
        convert_image(source, final_format=FinalFormat.WEBP)


def test_sixteen_bit_gray_png_keeps_bytes_when_format_matches():
    source = _gray16_png([0x0000, 0x0102, 0x7FFF, 0xFFFF])

    result = convert_image(source, final_format=FinalFormat.PNG)

    assert result.converted is False
    assert result.data == source
    assert result.mime_type == PNG_MIME_TYPE


def test_cmyk_jpeg_is_refused_for_8_bit_targets_instead_of_silently_recolored():
    # Сведение CMYK→RGB меняет цветовое управление, поэтому результат не
    # выдаётся под видом честной конвертации ни в PNG, ни в WebP.
    source = _encode(Image.new("CMYK", (3, 2), (0, 0, 0, 0)), "JPEG")

    for final_format in (FinalFormat.PNG, FinalFormat.WEBP):
        with pytest.raises(UnsupportedImageModeError):
            convert_image(source, final_format=final_format)


def test_cmyk_jpeg_keeps_bytes_when_requested_format_is_jpeg():
    source = _encode(Image.new("CMYK", (3, 2), (0, 0, 0, 0)), "JPEG")

    result = convert_image(source, final_format=FinalFormat.JPEG)

    assert result.converted is False
    assert result.data == source
    assert result.mime_type == "image/jpeg"


def test_unsupported_image_mode_error_is_typed():
    # Ошибка режима остаётся частью общей иерархии конвертации, поэтому вызывающая
    # сторона различает «нельзя закодировать» и «нельзя декодировать» по типу.
    assert issubclass(UnsupportedImageModeError, ImageConversionError)
