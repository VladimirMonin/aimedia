"""Синтетические изображения для offline-тестов подготовки входов.

Байты собираются вручную по спецификациям контейнеров: тесты не зависят от
Pillow и не требуют бинарных fixtures в репозитории. Каждый builder создаёт
структурно корректный файл; парные builders ломают ровно одну проверяемую
инварианту (CRC, длину, терминатор, кадр), чтобы падение доказывало конкретную
проверку, а не случайное совпадение.

Файл не является pytest-suite: он живёт в `tests/support`, добавляется в
`sys.path` только из `tests/conftest.py` и не импортируется production-кодом.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_PNG_MINIMAL_IHDR = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Собрать PNG-chunk с корректным CRC."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


# --- PNG ---------------------------------------------------------------------


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    """Валидный 8-битный RGB PNG заданного размера."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(width * 3) for _ in range(height))
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw))
        + png_chunk(b"IEND", b"")
    )


def interlaced_png_bytes(width: int = 2, height: int = 2) -> bytes:
    """PNG с Adam7-развёрткой: объём IDAT зависит от проходов."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 1)
    idat = zlib.compress(b"\x00" + bytes(width * 3))
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def png_with_invalid_ihdr(ihdr: bytes) -> bytes:
    """PNG с произвольным содержимым IHDR: длина и поля задаются тестом."""
    return PNG_SIGNATURE + png_chunk(b"IHDR", ihdr) + png_chunk(b"IEND", b"")


def png_with_bad_crc() -> bytes:
    """PNG с повреждённым CRC у IDAT."""
    bad_idat = struct.pack(">I", 4) + b"IDAT" + bytes(4) + struct.pack(">I", 0)
    return (
        PNG_SIGNATURE + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR) + bad_idat + png_chunk(b"IEND", b"")
    )


def png_with_short_idat() -> bytes:
    """PNG, где распакованный IDAT не покрывает всю геометрию изображения."""
    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(3))
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def png_with_undecodable_idat() -> bytes:
    """PNG, IDAT которого не распаковывается как zlib-поток."""
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR)
        + png_chunk(b"IDAT", b"\x00\x01\x02\x03")
        + png_chunk(b"IEND", b"")
    )


def truncated_png() -> bytes:
    """PNG без IEND: файл оборван посередине."""
    return PNG_SIGNATURE + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR)


def png_without_iend() -> bytes:
    """PNG с валидным IHDR/IDAT, но без IEND."""
    raw = b"\x00" + bytes(6) + b"\x00" + bytes(6)
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR)
        + png_chunk(b"IDAT", zlib.compress(raw))
    )


def png_without_idat() -> bytes:
    """PNG с валидным IHDR, но без пиксельных данных IDAT."""
    return PNG_SIGNATURE + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR) + png_chunk(b"IEND", b"")


def png_without_ihdr_first() -> bytes:
    """PNG, где перед IHDR стоит другой chunk."""
    return PNG_SIGNATURE + png_chunk(b"tEXt", b"x")


def png_with_duplicate_ihdr() -> bytes:
    """PNG с двумя IHDR подряд."""
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR)
        + png_chunk(b"IHDR", _PNG_MINIMAL_IHDR)
        + png_chunk(b"IEND", b"")
    )


def png_with_non_alpha_chunk_name() -> bytes:
    """PNG, где имя chunk'а содержит недопустимые символы."""
    return PNG_SIGNATURE + struct.pack(">I", 0) + b"IH\x00R" + struct.pack(">I", 0)


def png_with_chunk_beyond_file() -> bytes:
    """PNG, где заявленная длина chunk'а выходит за пределы файла."""
    return PNG_SIGNATURE + struct.pack(">I", 199) + b"IHDR" + bytes(4)


def png_with_truncated_chunk_header() -> bytes:
    """PNG, обрывающийся внутри заголовка chunk'а."""
    return PNG_SIGNATURE + bytes(3)


# --- JPEG --------------------------------------------------------------------


def jpeg_bytes(width: int = 2, height: int = 2) -> bytes:
    """Структурно корректный JPEG: SOI, SOF0, SOS и EOI без энтропийных данных."""
    return b"\xff\xd8" + _jpeg_sof(width, height) + _JPEG_SOS + b"\xff\xd9"


def _jpeg_sof(width: int, height: int) -> bytes:
    return (
        b"\xff\xc0"
        + struct.pack(">H", 11)
        + bytes([8])
        + struct.pack(">HH", height, width)
        + bytes([1, 1, 0x11, 0])
    )


_JPEG_SOS = b"\xff\xda" + struct.pack(">H", 8) + bytes([1, 1, 0x00, 0, 63, 0])


def jpeg_without_eoi() -> bytes:
    """JPEG без терминатора EOI."""
    return jpeg_bytes().removesuffix(b"\xff\xd9")


def jpeg_without_sof() -> bytes:
    """JPEG без кадра: есть только SOS и EOI, поэтому размеры неизвестны."""
    return b"\xff\xd8" + _JPEG_SOS + b"\xff\xd9"


def jpeg_with_zero_dimensions() -> bytes:
    """JPEG с нулевыми размерами кадра."""
    return b"\xff\xd8" + _jpeg_sof(0, 0) + b"\xff\xd9"


def jpeg_with_short_sof() -> bytes:
    """JPEG, у которого SOF-сегмент короче минимальных 6 байт данных."""
    return b"\xff\xd8" + b"\xff\xc0" + struct.pack(">H", 4) + b"\x08\x00" + b"\xff\xd9"


def jpeg_with_invalid_segment_length() -> bytes:
    """JPEG с длиной сегмента меньше двух — структура уже некорректна."""
    return b"\xff\xd8" + b"\xff\xe0\x00\x01" + b"\xff\xd9"


def jpeg_without_start_marker() -> bytes:
    """Последовательность с SOI и EOI, но без валидного маркера сегмента."""
    return b"\xff\xd8" + b"\x00\x00" + b"\xff\xd9"


def jpeg_with_truncated_segment_header() -> bytes:
    """JPEG, обрывающийся внутри заголовка сегмента."""
    return b"\xff\xd8\xff\xe0\xff\xd9"


def jpeg_with_segment_beyond_file() -> bytes:
    """JPEG, где длина сегмента выходит за пределы файла."""
    return b"\xff\xd8\xff\xe0\xff\xff\xff\xd9"


def jpeg_with_filler_bytes() -> bytes:
    """JPEG с 0xFF-заполнителем и restart-маркером до SOF."""
    return b"\xff\xd8" + b"\xff\xff" + b"\xff\xd0" + _jpeg_sof(2, 2) + b"\xff\xd9"


# --- WebP -------------------------------------------------------------------


def _webp_container(*chunks: tuple[bytes, bytes]) -> bytes:
    """Собрать RIFF/WEBP из chunk'ов, каждый выровнен по чётности."""
    body = b"".join(
        fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")
        for fourcc, payload in chunks
    )
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _webp_vp8x_payload(width: int, height: int, flags: int = 0) -> bytes:
    """Заголовок VP8X: флаги и размеры canvas минус один."""
    return (
        flags.to_bytes(4, "little")
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


def _webp_vp8l_payload(width: int, height: int) -> bytes:
    """Кадр VP8L: сигнатура 0x2F и упакованные размеры."""
    bits = (width - 1) | ((height - 1) << 14)
    return b"\x2f" + bits.to_bytes(4, "little")


def webp_lossless_bytes(width: int = 2, height: int = 2) -> bytes:
    """Валидный RIFF/WEBP с кадром VP8L заданного размера."""
    return _webp_container((b"VP8L", _webp_vp8l_payload(width, height)))


def webp_extended_bytes(width: int = 3, height: int = 5) -> bytes:
    """Валидный расширенный WebP: заголовок VP8X и фактический кадр VP8L.

    Одного VP8X недостаточно: он задаёт canvas и флаги, но не пиксельный кадр.
    """
    return _webp_container(
        (b"VP8X", _webp_vp8x_payload(width, height)),
        (b"VP8L", _webp_vp8l_payload(width, height)),
    )


def webp_extended_without_frame_bytes(width: int = 3, height: int = 5) -> bytes:
    """Расширенный WebP с одним VP8X и без кадра VP8/VP8L: кадра нет."""
    return _webp_container((b"VP8X", _webp_vp8x_payload(width, height)))


def webp_animated_bytes(width: int = 3, height: int = 5) -> bytes:
    """Анимированный WebP: VP8X с флагом ANIM и ANMF, но без статичного кадра.

    Локально анимация не поддерживается: заголовок VP8X не заменяет кадр.
    """
    animation_flag = 0b00000010
    anim = bytes(4)
    anmf = bytes(16)
    return _webp_container(
        (b"VP8X", _webp_vp8x_payload(width, height, flags=animation_flag)),
        (b"ANIM", anim),
        (b"ANMF", anmf),
    )


def webp_lossy_bytes(width: int = 4, height: int = 6) -> bytes:
    """Валидный WebP с кадром VP8 (lossy)."""
    payload = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height) + bytes(4)
    return _webp_container((b"VP8 ", payload))


def webp_with_wrong_riff_size() -> bytes:
    """WebP, у которого заявленный размер RIFF не совпадает с файлом."""
    data = webp_lossless_bytes()
    return data[:4] + struct.pack("<I", 999) + data[8:]


def webp_without_frame() -> bytes:
    """Контейнер RIFF/WEBP без VP8/VP8L: формат не подтверждён."""
    return _webp_container((b"JUNK", b"ABCD"))


def webp_truncated_header() -> bytes:
    """Обрезанный RIFF-заголовок короче 12 байт."""
    return b"RIFF\x04\x00\x00\x00WEB"


def webp_header_without_chunks() -> bytes:
    """RIFF/WEBP с корректным заголовком, но без единого chunk'а."""
    return b"RIFF" + struct.pack("<I", 4) + b"WEBP"


def webp_with_truncated_chunk_header() -> bytes:
    """WebP, обрывающийся внутри заголовка chunk'а."""
    return b"RIFF" + struct.pack("<I", 6) + b"WEBP" + b"VP8L\x00\x00"


def webp_with_chunk_beyond_file() -> bytes:
    """WebP, где длина chunk'а выходит за пределы файла."""
    chunk = b"VP8L" + struct.pack("<I", 500) + b"\x2f\x00"
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


def webp_with_short_vp8x() -> bytes:
    """WebP с VP8X-заголовком короче требуемых 10 байт."""
    return _webp_container((b"VP8X", b"\x00\x00\x00"))


def webp_short_vp8_header() -> bytes:
    """WebP с VP8-кадром без корректной сигнатуры \\x9d\\x01\\x2a."""
    return _webp_container((b"VP8 ", bytes(10)))


def webp_with_bad_vp8l_header() -> bytes:
    """WebP, у которого VP8L-заголовок не начинается с сигнатуры 0x2F."""
    return _webp_container((b"VP8L", bytes(5)))
