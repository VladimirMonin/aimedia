"""Определение фактического типа изображения по содержимому и его размеров.

Расширение файла не является доказательством формата: файл с `.png` может быть
текстом или обрезанным изображением. Поэтому тип определяется по сигнатуре
контейнера, а затем контейнер структурно проверяется целиком:

- PNG — сигнатура, последовательность chunk'ов с CRC (IHDR первым, IEND последним)
  и распаковываемые IDAT, объём которых соответствует геометрии изображения;
- JPEG — маркеры сегментов, SOF, непустые SOS-сканы и терминатор EOI;
- WebP — контейнер RIFF/WEBP с корректными размерами chunk'ов и присутствием
  кадра VP8/VP8L; VP8X — расширение контейнера, а не кадр, поэтому одного VP8X
  недостаточно (анимированный WebP локально не поддерживается).

Это не перекодирование и не полный raster-decode: цель — отклонить повреждённый
файл и не отправить провайдеру заведомо невалидный вход (`08-job-execution.md`,
«Pre-submit validation»). Локальная обработка и конвертация изображений —
отдельный этап E05.

Модуль зависит только от стандартной библиотеки: доменные DTO остаются IO-free, а
Pillow не требуется для проверки структуры входа.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

PNG_MIME_TYPE = "image/png"
JPEG_MIME_TYPE = "image/jpeg"
WEBP_MIME_TYPE = "image/webp"

SUPPORTED_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {PNG_MIME_TYPE, JPEG_MIME_TYPE, WEBP_MIME_TYPE}
)
"""Подтверждённые локальные форматы входа: PNG, JPEG и WebP (D08/`06`).

Набор описывает локальную поддержку проекта, а не выдуманный лимит конкретной
модели: модельные ограничения проверяются Model Registry (E03).
"""

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

# Допустимые пары (bit depth, color type) по спецификации PNG: grayscale (0)
# допускает 1/2/4/8/16, truecolor (2) — 8/16, indexed (3) — 1/2/4/8,
# grayscale+alpha (4) и truecolor+alpha (6) — 8/16. Значения вне набора означают
# повреждённый или неподдерживаемый IHDR.
_PNG_IHDR_COMBINATIONS: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 0),
        (2, 0),
        (4, 0),
        (8, 0),
        (16, 0),
        (8, 2),
        (16, 2),
        (1, 3),
        (2, 3),
        (4, 3),
        (8, 3),
        (8, 4),
        (16, 4),
        (8, 6),
        (16, 6),
    }
)

# Число каналов на пиксель по color type PNG: grayscale, truecolor, indexed,
# grayscale+alpha, truecolor+alpha.
_PNG_CHANNELS: dict[int, int] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# Семь проходов Adam7: (x_start, y_start, x_step, y_step). Проход отдаёт только
# те пиксели, координаты которых попадают в его решётку, поэтому объём IDAT
# interlaced PNG определяется суммой строк всех непустых проходов.
_ADAM7_PASSES: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)

# Допустимые байты фильтра scanline: 0–4 по спецификации PNG.
_PNG_MAX_FILTER_BYTE = 4
_PNG_STREAM_CHUNK_SIZE = 64 * 1024


class InvalidImageContentError(Exception):
    """Содержимое не является структурно корректным изображением.

    Это ошибка подготовки входа, а не доменная ошибка: application переводит её в
    `UnsupportedInputFormatError` с понятным пользователю сообщением.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class ImageProbe:
    """Фактические характеристики изображения, полученные из его байтов."""

    mime_type: str
    width: int
    height: int


def probe_image(content: bytes) -> ImageProbe:
    """Определить формат по содержимому и проверить структуру контейнера.

    Поднимает `InvalidImageContentError`, если сигнатура не распознана или
    контейнер повреждён (обрезан, с неверным CRC/размером chunk'а, без нужных
    секций). Возвращает MIME и размеры в пикселях.
    """
    if content.startswith(_PNG_SIGNATURE):
        width, height = _probe_png(content)
        return ImageProbe(mime_type=PNG_MIME_TYPE, width=width, height=height)
    if content.startswith(_JPEG_SOI):
        width, height = _probe_jpeg(content)
        return ImageProbe(mime_type=JPEG_MIME_TYPE, width=width, height=height)
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        width, height = _probe_webp(content)
        return ImageProbe(mime_type=WEBP_MIME_TYPE, width=width, height=height)
    raise InvalidImageContentError(
        "Содержимое не распознано как PNG, JPEG или WebP по сигнатуре контейнера."
    )


def _probe_png(content: bytes) -> tuple[int, int]:
    """Полностью проверить PNG и вернуть размеры из IHDR.

    Помимо цепочки chunk'ов и их CRC распакованные данные IDAT обязаны давать
    ожидаемый объём отфильтрованных scanline'ов: для non-interlaced — по одному
    на строку изображения, для Adam7 — по геометрии семи проходов. Поэтому
    обрезанный или подменённый IDAT не проходит как валидный вход.
    """
    offset = len(_PNG_SIGNATURE)
    ihdr: tuple[int, int, int, int, int] | None = None
    idat_parts: list[bytes] = []
    seen_plte = False
    idat_finished = False
    seen_iend = False

    while offset < len(content):
        if offset + 8 > len(content):
            raise InvalidImageContentError("PNG обрезан: неполный заголовок chunk'а.")
        (length,) = struct.unpack(">I", content[offset : offset + 4])
        chunk_type = content[offset + 4 : offset + 8]
        if not chunk_type.isalpha():
            raise InvalidImageContentError("PNG повреждён: некорректное имя chunk'а.")
        data_start = offset + 8
        crc_start = data_start + length
        if crc_start + 4 > len(content):
            raise InvalidImageContentError("PNG обрезан: chunk выходит за пределы файла.")
        data = content[data_start:crc_start]
        (expected_crc,) = struct.unpack(">I", content[crc_start : crc_start + 4])
        actual_crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise InvalidImageContentError(
                f"PNG повреждён: неверный CRC chunk'а {chunk_type.decode('ascii', 'replace')}."
            )
        if ihdr is None and chunk_type != b"IHDR":
            raise InvalidImageContentError("PNG повреждён: первым обязан идти IHDR.")
        if idat_parts and chunk_type != b"IDAT":
            idat_finished = True
        if chunk_type == b"IHDR":
            if ihdr is not None:
                raise InvalidImageContentError("PNG повреждён: повторный IHDR.")
            ihdr = _png_ihdr(data)
        elif chunk_type == b"PLTE":
            assert ihdr is not None
            if seen_plte or idat_parts:
                raise InvalidImageContentError(
                    "PNG повреждён: PLTE повторяется или стоит после IDAT."
                )
            if ihdr[3] in (0, 4):
                raise InvalidImageContentError("PNG повреждён: PLTE недопустим для grayscale.")
            entries = length // 3
            if length == 0 or length % 3 or entries > 256:
                raise InvalidImageContentError("PNG повреждён: неверная длина PLTE.")
            if ihdr[3] == 3 and entries > 1 << ihdr[2]:
                raise InvalidImageContentError("PNG повреждён: PLTE превышает bit depth.")
            seen_plte = True
        elif chunk_type == b"IDAT":
            assert ihdr is not None
            if ihdr[3] == 3 and not seen_plte:
                raise InvalidImageContentError("PNG повреждён: indexed PNG требует PLTE до IDAT.")
            if idat_finished:
                raise InvalidImageContentError("PNG повреждён: IDAT должны идти подряд.")
            idat_parts.append(data)
        elif chunk_type == b"IEND":
            if length != 0:
                raise InvalidImageContentError("PNG повреждён: IEND обязан быть пустым.")
            seen_iend = True
            offset = crc_start + 4
            break
        elif chunk_type[:1].isupper() and chunk_type != b"PLTE":
            raise InvalidImageContentError("PNG повреждён: неизвестный критический chunk.")
        offset = crc_start + 4

    if ihdr is None:
        raise InvalidImageContentError("PNG повреждён: отсутствует IHDR.")
    if not seen_iend:
        raise InvalidImageContentError("PNG обрезан: отсутствует IEND.")
    if offset != len(content):
        raise InvalidImageContentError("PNG повреждён: данные после IEND.")
    if not idat_parts:
        raise InvalidImageContentError("PNG повреждён: отсутствуют данные IDAT.")
    _png_validate_pixels(ihdr, idat_parts)
    return ihdr[0], ihdr[1]


def _png_ihdr(data: bytes) -> tuple[int, int, int, int, int]:
    """Разобрать IHDR: (width, height, bit_depth, color_type, interlace)."""
    if len(data) != 13:
        raise InvalidImageContentError("PNG повреждён: IHDR неверной длины.")
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", data
    )
    if width == 0 or height == 0:
        raise InvalidImageContentError("PNG повреждён: нулевые размеры изображения.")
    if (bit_depth, color_type) not in _PNG_IHDR_COMBINATIONS:
        raise InvalidImageContentError(
            "PNG повреждён: недопустимое сочетание bit depth и color type."
        )
    if compression != 0 or filter_method != 0:
        raise InvalidImageContentError("PNG повреждён: неизвестный метод сжатия или фильтрации.")
    if interlace not in (0, 1):
        raise InvalidImageContentError("PNG повреждён: неизвестный способ развёртки.")
    return width, height, bit_depth, color_type, interlace


def _png_validate_pixels(ihdr: tuple[int, int, int, int, int], idat_parts: list[bytes]) -> None:
    """Проверить zlib-поток по строкам, не материализуя распакованный raster."""
    width, height, bit_depth, color_type, interlace = ihdr
    bits_per_pixel = bit_depth * _PNG_CHANNELS[color_type]
    if interlace == 1:
        passes = _png_adam7_layout(width, height, bits_per_pixel)
    else:
        passes = [((width * bits_per_pixel + 7) // 8 + 1, height)]
    expected = sum(row_size * rows for row_size, rows in passes)
    compressed_chunks = (
        part[start : start + _PNG_STREAM_CHUNK_SIZE]
        for part in idat_parts
        for start in range(0, len(part), _PNG_STREAM_CHUNK_SIZE)
    )
    inflater = zlib.decompressobj()
    decoded_bytes = 0
    pass_index = 0
    row_index = 0
    row_offset = 0
    try:
        for compressed in compressed_chunks:
            while True:
                raw = inflater.decompress(compressed, _PNG_STREAM_CHUNK_SIZE)
                compressed = inflater.unconsumed_tail
                if decoded_bytes + len(raw) > expected:
                    raise InvalidImageContentError("PNG повреждён: лишние данные scanline в IDAT.")
                decoded_bytes += len(raw)
                offset = 0
                while offset < len(raw):
                    row_size, rows = passes[pass_index]
                    if row_offset == 0 and raw[offset] > _PNG_MAX_FILTER_BYTE:
                        raise InvalidImageContentError(
                            "PNG повреждён: недопустимый байт фильтра scanline."
                        )
                    count = min(len(raw) - offset, row_size - row_offset)
                    offset += count
                    row_offset += count
                    if row_offset == row_size:
                        row_offset = 0
                        row_index += 1
                        if row_index == rows:
                            row_index = 0
                            pass_index += 1
                if inflater.eof:
                    if inflater.unused_data or compressed or any(compressed_chunks):
                        raise InvalidImageContentError(
                            "PNG повреждён: данные после zlib-потока IDAT."
                        )
                    break
                if not compressed and len(raw) < _PNG_STREAM_CHUNK_SIZE:
                    break
            if inflater.eof:
                break
    except zlib.error as exc:
        raise InvalidImageContentError("PNG повреждён: IDAT не распаковывается.") from exc
    if not inflater.eof or decoded_bytes != expected:
        raise InvalidImageContentError(
            "PNG повреждён: распакованные данные не соответствуют геометрии изображения."
        )


def _adam7_pass_size(
    width: int, height: int, x_start: int, y_start: int, x_step: int, y_step: int
) -> tuple[int, int]:
    """Ширина и высота прохода Adam7: проход без пикселей не даёт ни строки."""
    pass_width = (width - x_start + x_step - 1) // x_step if width > x_start else 0
    pass_height = (height - y_start + y_step - 1) // y_step if height > y_start else 0
    return pass_width, pass_height


def _png_adam7_layout(width: int, height: int, bits_per_pixel: int) -> list[tuple[int, int]]:
    """Размер строки с фильтром и число строк каждого непустого прохода Adam7."""
    passes = []
    for x_start, y_start, x_step, y_step in _ADAM7_PASSES:
        pass_width, pass_height = _adam7_pass_size(width, height, x_start, y_start, x_step, y_step)
        if pass_width and pass_height:
            passes.append(((pass_width * bits_per_pixel + 7) // 8 + 1, pass_height))
    return passes


def _probe_jpeg(content: bytes) -> tuple[int, int]:
    """Проверить структуру маркеров, SOF и непустых SOS-сканов до EOI."""
    if len(content) < 4:
        raise InvalidImageContentError("JPEG обрезан: файл короче минимального.")
    if not content.endswith(_JPEG_EOI):
        raise InvalidImageContentError("JPEG обрезан: отсутствует маркер EOI.")

    offset = 2  # после SOI
    dims: tuple[int, int] | None = None
    components: frozenset[int] = frozenset()
    saw_scan = False
    in_scan = False
    scan_payload = False
    total = len(content)

    while offset < total:
        if in_scan and content[offset] != 0xFF:
            scan_payload = True
            offset += 1
            continue
        if content[offset] != 0xFF:
            raise InvalidImageContentError("JPEG повреждён: ожидался маркер сегмента.")
        offset += 1
        # Заполнители 0xFF допустимы как перед сегментами, так и в скане.
        while offset < total and content[offset] == 0xFF:
            offset += 1
        if offset >= total:
            raise InvalidImageContentError("JPEG обрезан: неполный маркер.")
        marker = content[offset]
        offset += 1
        if in_scan:
            if marker == 0x00:  # stuffed FF — один байт энтропийных данных
                scan_payload = True
                continue
            if 0xD0 <= marker <= 0xD7:  # restart внутри скана
                continue
            if not scan_payload:
                raise InvalidImageContentError("JPEG повреждён: пустой скан SOS.")
            in_scan = False
        elif marker == 0x00 or 0xD0 <= marker <= 0xD7:
            raise InvalidImageContentError("JPEG повреждён: маркер скана вне SOS.")
        if marker == 0xD9:
            if not saw_scan or offset != total or dims is None:
                raise InvalidImageContentError("JPEG повреждён: EOI до скана или лишние данные.")
            return dims
        if marker == 0xD8:
            raise InvalidImageContentError("JPEG повреждён: повторный SOI.")
        if marker == 0x01:  # TEM не несёт длины
            continue
        if offset + 2 > total:
            raise InvalidImageContentError("JPEG обрезан: неполный заголовок сегмента.")
        (segment_length,) = struct.unpack(">H", content[offset : offset + 2])
        if segment_length < 2:
            raise InvalidImageContentError("JPEG повреждён: длина сегмента меньше 2.")
        segment_end = offset + segment_length
        if segment_end > total:
            raise InvalidImageContentError("JPEG обрезан: сегмент выходит за пределы файла.")
        data = content[offset + 2 : segment_end]
        if _is_jpeg_sof(marker):
            if dims is not None or saw_scan:
                raise InvalidImageContentError("JPEG повреждён: повторный SOF.")
            dims, components = _jpeg_sof_dimensions(data)
        elif marker == 0xDA:
            if dims is None:
                raise InvalidImageContentError("JPEG повреждён: SOS до SOF.")
            _validate_jpeg_sos(data, components)
            saw_scan = True
            in_scan = True
            scan_payload = False
        offset = segment_end

    raise InvalidImageContentError("JPEG обрезан: отсутствует маркер EOI.")


def _is_jpeg_sof(marker: int) -> bool:
    # SOF0–SOF15, кроме DHT (0xC4), JPG (0xC8) и DAC (0xCC).
    return 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)


def _jpeg_sof_dimensions(data: bytes) -> tuple[tuple[int, int], frozenset[int]]:
    if len(data) < 6 or data[5] == 0 or len(data) != 6 + 3 * data[5]:
        raise InvalidImageContentError("JPEG повреждён: SOF неверной длины.")
    height, width = struct.unpack(">HH", data[1:5])
    if width == 0 or height == 0:
        raise InvalidImageContentError("JPEG повреждён: нулевые размеры изображения.")
    components = frozenset(data[6::3])
    if len(components) != data[5]:
        raise InvalidImageContentError("JPEG повреждён: повторные компоненты SOF.")
    return (width, height), components


def _validate_jpeg_sos(data: bytes, components: frozenset[int]) -> None:
    if len(data) < 4 or data[0] == 0 or len(data) != 4 + 2 * data[0]:
        raise InvalidImageContentError("JPEG повреждён: SOS неверной длины.")
    selectors = [data[index] for index in range(1, 1 + 2 * data[0], 2)]
    if len(set(selectors)) != len(selectors) or not set(selectors) <= components:
        raise InvalidImageContentError("JPEG повреждён: SOS с неизвестной компонентой.")


def _probe_webp(content: bytes) -> tuple[int, int]:
    """Проверить контейнер RIFF/WEBP и вернуть размеры фактического кадра.

    VP8X задаёт флаги и размеры canvas расширенного контейнера, но пиксельного
    изображения не содержит: расширенный WebP принимается только при наличии
    кадра VP8/VP8L. Анимация по флагу VP8X или chunk'ам ANIM/ANMF локально
    не поддерживается, даже если контейнер также содержит статичный кадр.
    """
    if len(content) < 12:
        raise InvalidImageContentError("WebP обрезан: неполный RIFF-заголовок.")
    (riff_size,) = struct.unpack("<I", content[4:8])
    if riff_size + 8 != len(content):
        raise InvalidImageContentError("WebP повреждён: размер RIFF не совпадает с файлом.")

    offset = 12
    frame_dims: tuple[int, int] | None = None
    vp8x_canvas: tuple[int, int] | None = None
    total = len(content)
    while offset < total:
        if offset + 8 > total:
            raise InvalidImageContentError("WebP обрезан: неполный заголовок chunk'а.")
        fourcc = content[offset : offset + 4]
        (chunk_size,) = struct.unpack("<I", content[offset + 4 : offset + 8])
        data_start = offset + 8
        padded_end = data_start + chunk_size + (chunk_size & 1)
        if padded_end > total:
            raise InvalidImageContentError("WebP обрезан: chunk выходит за пределы файла.")
        payload = content[data_start : data_start + chunk_size]
        if fourcc == b"VP8X":
            vp8x_canvas = _webp_vp8x_dimensions(payload)
            if payload[0] & 0x02:
                raise InvalidImageContentError("Анимированный WebP не поддерживается.")
        elif fourcc in (b"ANIM", b"ANMF"):
            raise InvalidImageContentError("Анимированный WebP не поддерживается.")
        elif fourcc == b"VP8L" and frame_dims is None:
            frame_dims = _webp_vp8l_dimensions(payload)
        elif fourcc == b"VP8 " and frame_dims is None:
            frame_dims = _webp_vp8_dimensions(payload)
        offset = padded_end

    if frame_dims is None:
        if vp8x_canvas is not None:
            raise InvalidImageContentError(
                "WebP повреждён: VP8X без кадра VP8/VP8L; анимированный WebP не поддерживается."
            )
        raise InvalidImageContentError("WebP повреждён: отсутствует кадр VP8/VP8L.")
    return frame_dims


def _webp_vp8x_dimensions(payload: bytes) -> tuple[int, int]:
    """Размеры canvas из заголовка VP8X: они лишь проверяются, кадром не являются."""
    if len(payload) < 10:
        raise InvalidImageContentError("WebP повреждён: VP8X неверной длины.")
    width = 1 + int.from_bytes(payload[4:7], "little")
    height = 1 + int.from_bytes(payload[7:10], "little")
    return width, height


def _webp_vp8l_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 5 or payload[0] != 0x2F:
        raise InvalidImageContentError("WebP повреждён: некорректный VP8L-заголовок.")
    bits = int.from_bytes(payload[1:5], "little")
    width = (bits & 0x3FFF) + 1
    height = ((bits >> 14) & 0x3FFF) + 1
    return width, height


def _webp_vp8_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
        raise InvalidImageContentError("WebP повреждён: некорректный VP8-заголовок.")
    width, height = struct.unpack("<HH", payload[6:10])
    return width & 0x3FFF, height & 0x3FFF
