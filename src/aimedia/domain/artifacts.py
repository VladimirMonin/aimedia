"""Артефакты домена: сохранённый локальный результат и ссылка provider.

`Artifact` описывает **фактически сохранённый** файл: локальный путь и метаданные
реальных байтов. Remote URL — только provenance/источник получения, а не
постоянный результат file-producing Job (инвариант 11).
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.base import (
    DomainModel,
    LocalPath,
    MimeType,
    NonEmptyStr,
    Sha256Hex,
)


class ArtifactKind(StrEnum):
    """Тип артефакта.

    В v0.1 создаются только изображения; новые значения добавляются вместе с
    появлением соответствующей функциональности (`03-domain-model.md`).
    """

    IMAGE = "image"


class ArtifactRole(StrEnum):
    """Роль артефакта внутри Job.

    `--keep-original` сохраняет provider-original отдельной ролью, поэтому роль
    различает конечный результат и исходные байты provider.
    """

    FINAL = "final"
    ORIGINAL = "original"


class Artifact(DomainModel):
    """Сохранённый и проверенный локальный результат."""

    kind: ArtifactKind
    role: ArtifactRole = ArtifactRole.FINAL

    local_path: LocalPath | None = None
    remote_url: str | None = None

    mime_type: MimeType | None = None
    size_bytes: int | None = None
    sha256: Sha256Hex | None = None

    metadata: dict[str, object] = {}


class RemoteArtifact(DomainModel):
    """Ссылка provider на результат до локального сохранения.

    Между `RemoteArtifact` и `Artifact` стоят download/decode, при необходимости
    конвертация и проверка фактического файла (E05). Вид доставки (URL, base64,
    provider file id) нормализуется adapter, а не доменом.
    """

    kind: ArtifactKind
    url: str | None = None
    base64_data: str | None = None
    content_type: MimeType | None = None
    provider_file_id: NonEmptyStr | None = None
    metadata: dict[str, object] = {}
