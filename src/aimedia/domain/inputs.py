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

from pydantic import model_validator

from aimedia.domain.base import (
    DomainModel,
    LocalPath,
    ManagedRelativePath,
    MimeType,
    NonBlankStr,
    Sha256Hex,
)

_ERROR_MANAGED_COPY_METADATA = (
    "managed-копия входа требует заполненных sha256, mime_type и size_bytes"
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

    `path` — provenance исходного файла и не подменяется managed-копией. Копия
    живёт в отдельном необязательном `managed_path`: относительный путь внутри
    managed-дерева app data (`inputs/<job_id>/<position>.<ext>`), проверяемый
    лексически без обращения к файловой системе. Отсутствие `managed_path`
    означает legacy-запись без копии, а не ошибку; байты копии остаются в
    файловом слое и не попадают ни в домен, ни в SQLite.

    Заданный `managed_path` требует заполненных `sha256`, `mime_type` и
    `size_bytes`: копия делается из проверенных байтов, и ссылка без них не
    доказывала бы соответствие копии входу.
    """

    kind: InputKind
    path: LocalPath
    position: int

    mime_type: MimeType | None = None
    size_bytes: int | None = None
    sha256: Sha256Hex | None = None
    managed_path: ManagedRelativePath | None = None

    metadata: dict[str, object] = {}

    @model_validator(mode="after")
    def _managed_copy_requires_verified_metadata(self) -> InputRef:
        """Managed-копия без SHA-256, MIME или размера — ошибка, legacy — нет.

        Правило условное: вход без копии (`managed_path is None`) может не нести ни
        одного из этих полей и остаётся читаемым — история, сохранённая до schema v2,
        не переклассифицируется.
        """
        if self.managed_path is None:
            return self
        if self.sha256 is None or self.mime_type is None or self.size_bytes is None:
            raise ValueError(_ERROR_MANAGED_COPY_METADATA)
        if self.size_bytes < 0:
            raise ValueError("размер managed-копии входа не может быть отрицательным")
        return self


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
