"""Входные ресурсы и prompt-источники как доменные значения.

Здесь определены только DTO: входной ресурс, источник prompt и финальный
compiled prompt. Чтение файлов, определение MIME, вычисление SHA-256 и сама
компиляция prompt реализованы в application-слое C04
(`aimedia.application.prompts`, `aimedia.application.inputs`); домен описывает
форму данных, а не файловый pipeline.

История хранит и путь источника, и снимок его текста: изменение файла на диске
не должно менять уже отправленный модели prompt.
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.base import (
    DomainModel,
    LocalPath,
    MimeType,
    NonBlankStr,
    Sha256Hex,
)


class InputKind(StrEnum):
    """Тип входного ресурса.

    В v0.1 используется `IMAGE`; остальные значения появятся вместе со своими
    модулями, а не заранее.
    """

    IMAGE = "image"


class InputRef(DomainModel):
    """Ссылка на внешний входной ресурс задания.

    Позиция сохраняет порядок входов, `sha256` — обязательный пункт истории
    (решение baseline D15). `metadata` несёт derived-данные ресурса (например
    `width`/`height`), полученные при подготовке входа.
    """

    kind: InputKind
    path: LocalPath
    position: int

    mime_type: MimeType | None = None
    size_bytes: int | None = None
    sha256: Sha256Hex | None = None

    metadata: dict[str, object] = {}


class PromptSourceKind(StrEnum):
    """Источник текста: строка CLI или файл."""

    INLINE = "inline"
    FILE = "file"


class PromptSource(DomainModel):
    """Один источник prompt в исходном порядке CLI.

    Сохраняются и путь, и снимок текста: история должна показывать, какой текст
    реально ушёл модели, даже если файл позже изменён. Разделитель и сборка
    выполняются компилятором (`aimedia.application.prompts.compile`), поэтому
    здесь нет склеенного текста.
    """

    kind: PromptSourceKind
    text: str
    position: int
    path: LocalPath | None = None


class CompiledPrompt(DomainModel):
    """Точный текст, отправленный модели, и число его источников.

    Пустой текст недопустим: домен не отправляет модели пустой prompt.
    `sha256` фиксирует точный отправленный текст и остаётся derived-полем.
    """

    text: NonBlankStr
    source_count: int
    sha256: Sha256Hex | None = None
