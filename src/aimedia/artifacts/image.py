"""Локальная конвертация байтов изображения в конечный формат артефакта.

Модуль чистый: он не пишет файлы, не считает hash и не знает о БД. Публикация —
`aimedia.artifacts.output`, SHA-256 считается по уже опубликованному файлу (E05,
R05), а метаданные сохраняются отдельным шагом.

Правила этапа E05 (`docs/plans/README.md`, «Локальные artifacts и честная
конвертация»):

- формат определяется декодированием реальных байтов, а не расширением: Pillow
  открывает контейнер, `verify()` проверяет его структуру, полный `load()` читает
  raster. Дополнительно проверяется концевой маркер контейнера, потому что Pillow
  принимает PNG без IEND и JPEG без EOI: обрезанный файл не должен становиться
  валидным артефактом;
- лимиты байтов и пикселей применяются до аллокации растра, а предупреждение
  Pillow о decompression bomb превращается в ошибку;
- скрытое изменение размеров не выполняется: фактические размеры конечных байтов
  сверяются с исходными;
- PNG и WebP сохраняют alpha; для JPEG alpha неизбежно теряется, поэтому
  прозрачность накладывается на белый фон, а результат сообщает
  `alpha_flattened=True`;
- если исходный формат уже равен запрошенному, валидные байты сохраняются как
  есть: лишняя lossy-перекодировка уже корректного результата не выполняется;
- конечные байты декодируются повторно, и MIME/формат/размеры берутся из этого
  декодирования, а не из намерения.

Ошибки типизированы и не содержат ни байтов, ни путей: сообщение описывает
причину, а не входные данные.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from aimedia.domain.requests import FinalFormat

PNG_MIME_TYPE = "image/png"
JPEG_MIME_TYPE = "image/jpeg"
WEBP_MIME_TYPE = "image/webp"

MAX_INPUT_BYTES = 64 * 1024 * 1024
"""Локальный предел размера входных байтов: не модель-лимит, а защита от чтения
гигантского буфера в декодер. Provider-лимиты задаются отдельно."""

MAX_INPUT_PIXELS = 50_000_000
"""Локальный предел числа пикселей с запасом над крупнейшим генеративным
разрешением (4K ≈ 8.3 Мп) и ниже порога Pillow, чтобы отказ был типизированной
ошибкой проекта, а не предупреждением библиотеки."""

_JPEG_BACKGROUND = (255, 255, 255)

_PILLOW_FORMAT_BY_FINAL: dict[FinalFormat, str] = {
    FinalFormat.PNG: "PNG",
    FinalFormat.JPEG: "JPEG",
    FinalFormat.WEBP: "WEBP",
}
_FINAL_FORMAT_BY_PILLOW: dict[str, FinalFormat] = {
    pillow: final for final, pillow in _PILLOW_FORMAT_BY_FINAL.items()
}
_MIME_TYPE_BY_FINAL: dict[FinalFormat, str] = {
    FinalFormat.PNG: PNG_MIME_TYPE,
    FinalFormat.JPEG: JPEG_MIME_TYPE,
    FinalFormat.WEBP: WEBP_MIME_TYPE,
}

# Режимы, которые Pillow кодирует в формат без промежуточного преобразования.
_PNG_DIRECT_MODES = frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I;16"})
_JPEG_DIRECT_MODES = frozenset({"1", "L", "RGB", "CMYK"})

# Режимы, переходящие в 8-битный RGB/RGBA без искажения видимых данных.
_WEBP_CONVERTIBLE_MODES = frozenset({"1", "L", "LA", "P"})

# Концевые маркеры контейнеров, отсутствие которых Pillow не считает ошибкой.
_CONTAINER_TRAILERS: dict[FinalFormat, bytes] = {
    FinalFormat.PNG: b"\x00\x00\x00\x00IEND\xaeB`\x82",
    FinalFormat.JPEG: b"\xff\xd9",
}


class ImageConversionError(Exception):
    """Безопасная ошибка конвертации: сообщение не содержит байтов и путей."""


class UnsupportedImageInputError(ImageConversionError):
    """Байты не являются изображением поддерживаемого контейнера."""


class ImageDecodeError(ImageConversionError):
    """Контейнер распознан, но повреждён или обрезан."""


class ImageTooLargeError(ImageConversionError):
    """Размер файла или число пикселей превышает безопасный предел."""


class UnsupportedImageModeError(ImageConversionError):
    """Пиксельный режим нельзя закодировать в конечный формат без потери данных."""


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Проверенные конечные байты и их фактические характеристики.

    `width`/`height`/`mime_type` получены повторным декодированием конечных
    байтов, а не перенесены из намерения. `converted` различает перекодирование и
    сохранение исходных валидных байтов в том же формате; `alpha_flattened`
    сигнализирует ожидаемую потерю прозрачности при конвертации в JPEG.
    """

    data: bytes
    mime_type: str
    source_format: FinalFormat
    final_format: FinalFormat
    width: int
    height: int
    converted: bool
    alpha_flattened: bool


def convert_image(
    data: bytes,
    *,
    final_format: FinalFormat,
    max_bytes: int = MAX_INPUT_BYTES,
    max_pixels: int = MAX_INPUT_PIXELS,
) -> PreparedImage:
    """Проверить байты изображения и выдать их в запрошенном конечном формате.

    Границы аргументов проверяются здесь, а не у вызывающей стороны, чтобы
    нулевой лимит не превратился в молчаливое «любой размер допустим».
    """
    if max_bytes <= 0 or max_pixels <= 0:
        raise ValueError("Лимиты конвертации должны быть положительными")
    if not data:
        raise UnsupportedImageInputError("Пустые байты не являются изображением.")
    if len(data) > max_bytes:
        raise ImageTooLargeError(f"Размер изображения превышает предел {max_bytes} байт.")

    image, source_format = _decode_source(data, max_pixels=max_pixels)
    width, height = image.size
    if source_format is final_format:
        # Полностью декодированные валидные байты уже имеют запрошенный формат:
        # повторное кодирование только ухудшило бы результат.
        return PreparedImage(
            data=data,
            mime_type=_MIME_TYPE_BY_FINAL[final_format],
            source_format=source_format,
            final_format=final_format,
            width=width,
            height=height,
            converted=False,
            alpha_flattened=False,
        )

    prepared, alpha_flattened = _prepare_for_encode(image, final_format)
    final_bytes = _encode(prepared, final_format)
    actual_mime, actual_size = _verify_final_bytes(final_bytes, final_format)
    if actual_size != (width, height):
        raise ImageConversionError("Конвертация изменила размеры изображения.")
    return PreparedImage(
        data=final_bytes,
        mime_type=actual_mime,
        source_format=source_format,
        final_format=final_format,
        width=width,
        height=height,
        converted=True,
        alpha_flattened=alpha_flattened,
    )


def _decode_source(data: bytes, *, max_pixels: int) -> tuple[Image.Image, FinalFormat]:
    """Определить фактический формат и полностью декодировать raster.

    Предупреждение Pillow о подозрительно большом числе пикселей становится
    ошибкой: это предел памяти, а не косметическая диагностика.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        header_error: ImageConversionError | None = None
        try:
            header = Image.open(BytesIO(data))
        except UnidentifiedImageError:
            header_error = UnsupportedImageInputError(
                "Содержимое не распознано как поддерживаемое изображение."
            )
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            header_error = ImageTooLargeError(
                "Изображение превышает безопасный предел по числу пикселей."
            )
        except (OSError, ValueError):
            header_error = ImageDecodeError("Заголовок изображения повреждён или обрезан.")
        if header_error is not None:
            raise header_error

        pillow_format = header.format
        if pillow_format is None or pillow_format not in _FINAL_FORMAT_BY_PILLOW:
            raise UnsupportedImageInputError("Формат контейнера не поддерживается локально.")
        source_format = _FINAL_FORMAT_BY_PILLOW[pillow_format]
        width, height = header.size
        if width * height > max_pixels:
            raise ImageTooLargeError(f"Число пикселей изображения превышает предел {max_pixels}.")

        _require_intact_container(data, source_format)
        verify_failed = False
        try:
            header.verify()
        except (OSError, SyntaxError, ValueError):
            verify_failed = True
        if verify_failed:
            raise ImageDecodeError("Структура изображения повреждена.")
        decode_error: ImageConversionError | None = None
        try:
            image = Image.open(BytesIO(data))
            image.load()
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            decode_error = ImageTooLargeError(
                "Изображение превышает безопасный предел по числу пикселей."
            )
        except (OSError, SyntaxError, ValueError):
            decode_error = ImageDecodeError("Данные изображения не декодируются полностью.")
        if decode_error is not None:
            raise decode_error
    return image, source_format


def _require_intact_container(data: bytes, image_format: FinalFormat) -> None:
    """Проверить концевой маркер контейнера, который Pillow не требует.

    Pillow декодирует PNG без IEND и JPEG без EOI, поэтому без этой проверки
    обрезанный файл прошёл бы как полностью декодированное изображение. Для WebP
    роль маркера играет размер RIFF, объявленный в заголовке.
    """
    if image_format is FinalFormat.WEBP:
        if len(data) < 12 or int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ImageDecodeError("WebP обрезан: размер RIFF не совпадает с файлом.")
        return
    if not data.endswith(_CONTAINER_TRAILERS[image_format]):
        raise ImageDecodeError("Изображение обрезано: отсутствует концевой маркер контейнера.")


def _has_alpha(image: Image.Image) -> bool:
    """Есть ли у изображения фактическая прозрачность.

    У PNG в режимах P, RGB и L прозрачность хранится в `tRNS`, а не в
    отдельном канале; нулевое значение grayscale также означает прозрачность.
    """
    return "A" in image.getbands() or (
        image.mode in {"P", "RGB", "L"} and image.info.get("transparency") is not None
    )


def _prepare_for_encode(image: Image.Image, target: FinalFormat) -> tuple[Image.Image, bool]:
    """Привести пиксельный режим к целевому формату и сообщить о потере alpha.

    Преобразуются только режимы, которые переходят в 8-битный RGB/RGBA без
    искажения видимых данных: градации серого (1/L/LA), палитра и уже готовые
    RGB/RGBA. Цветовое управление CMYK и 16-битная градация отклоняются для
    любого конечного формата — молчаливое искажение цвета или полутонов хуже
    честной ошибки. Единственное документированное изменение — alpha для JPEG:
    прозрачность накладывается на белый фон, и это сообщается вызывающей стороне.
    """
    if target is FinalFormat.JPEG:
        if _has_alpha(image):
            return _flatten_on_white(image), True
        if image.mode in _JPEG_DIRECT_MODES:
            return image, False
        if image.mode == "P":
            return image.convert("RGB"), False
        raise UnsupportedImageModeError(
            f"Режим {image.mode!r} нельзя закодировать в JPEG без потери данных."
        )
    if target is FinalFormat.PNG:
        if image.mode in _PNG_DIRECT_MODES:
            return image, False
        raise UnsupportedImageModeError(
            f"Режим {image.mode!r} нельзя закодировать в PNG без потери данных."
        )
    if image.mode in {"RGB", "RGBA"}:
        return (image.convert("RGBA") if _has_alpha(image) else image), False
    if image.mode in _WEBP_CONVERTIBLE_MODES:
        return (image.convert("RGBA") if _has_alpha(image) else image.convert("RGB")), False
    raise UnsupportedImageModeError(
        f"Режим {image.mode!r} нельзя закодировать в WebP без потери данных."
    )


def _flatten_on_white(image: Image.Image) -> Image.Image:
    """Наложить прозрачность на белый фон: JPEG не хранит alpha."""
    rgba = image.convert("RGBA")
    flattened = Image.new("RGB", rgba.size, _JPEG_BACKGROUND)
    flattened.paste(rgba, mask=rgba.getchannel("A"))
    return flattened


def _encode(image: Image.Image, target: FinalFormat) -> bytes:
    buffer = BytesIO()
    try:
        if target is FinalFormat.WEBP and image.mode == "RGBA":
            # Lossy WebP искажает alpha, поэтому прозрачное изображение кодируется
            # lossless: сохранение alpha не должно быть приблизительным.
            image.save(buffer, _PILLOW_FORMAT_BY_FINAL[target], lossless=True)
        else:
            image.save(buffer, _PILLOW_FORMAT_BY_FINAL[target])
    except (OSError, ValueError):
        # Pillow сообщает о невыразимых параметрах (например, превышение
        # собственного предела WebP по стороне кадра) обычным ValueError.
        encode_failed = True
    else:
        encode_failed = False
    if encode_failed:
        raise ImageConversionError("Кодирование изображения в конечный формат не удалось.")
    return buffer.getvalue()


def _verify_final_bytes(data: bytes, expected: FinalFormat) -> tuple[str, tuple[int, int]]:
    """Декодировать конечные байты и вернуть их фактический MIME и размеры."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        final_error: ImageConversionError | None = None
        try:
            image = Image.open(BytesIO(data))
            image.load()
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            final_error = ImageConversionError("Конечные байты превышают безопасный предел.")
        except (OSError, SyntaxError, ValueError):
            final_error = ImageConversionError("Конечные байты не декодируются полностью.")
        if final_error is not None:
            raise final_error
    pillow_format = image.format
    if pillow_format is None or _FINAL_FORMAT_BY_PILLOW.get(pillow_format) is not expected:
        raise ImageConversionError("Фактический формат конечных байтов не совпал с запрошенным.")
    _require_intact_container(data, expected)
    return _MIME_TYPE_BY_FINAL[expected], image.size
