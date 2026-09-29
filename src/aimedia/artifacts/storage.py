"""Файловый adapter порта `ArtifactStorage`: валидация, конвертация, публикация.

Модуль связывает уже проверенные компоненты E05: `prepare_image`/`convert_image`
(фактические байты и метаданные) и `publish_output` (no-clobber публикация). Он
не знает ни о БД, ни о provider; безопасные события `artifact_converted`,
`artifact_saved`, `artifact_cleanup_failed` (C08c2a) пишутся через
необязательный `EventLogger`.

Правила хранения (`docs/plans/README.md` → E05, решения baseline D08/D09):

- managed-путь по умолчанию — `<data_root>/outputs/<job_id>/`, создаётся только
  этот управляемый каталог; в `Artifact.local_path` сохраняется путь **относительно**
  data-root, чтобы история не привязывалась к машине;
- явный пользовательский `output_dir` обязан уже существовать; в `local_path`
  сохраняется фактический абсолютный путь, а `ownership=user_output`. Скрытого
  двойного копирования нет: файл публикуется ровно один раз;
- формат и MIME определяются фактическими байтами, а не подсказкой `source_mime_type`
  и не расширением; роль `ORIGINAL` сохраняет исходные байты без конвертации, FINAL
  использует `final_format`, а при его отсутствии — фактический формат источника;
- метаданные описывают конечные опубликованные байты (размер, SHA-256, размеры,
  alpha, ownership); при отказе декодирования/кодирования/записи/публикации
  `Artifact` не возвращается, потому что ложный артефакт опаснее ошибки.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from aimedia.artifacts.events import (
    log_artifact_cleanup_failed,
    log_artifact_converted,
    log_artifact_saved,
)
from aimedia.artifacts.image import PreparedImage, convert_image, prepare_image
from aimedia.artifacts.output import PublishedOutput, publish_output
from aimedia.domain.artifacts import Artifact, ArtifactKind, ArtifactRole
from aimedia.domain.requests import FinalFormat
from aimedia.logging import EventLogger

OUTPUTS_DIRNAME = "outputs"
"""Каталог managed-результатов внутри data-root (`outputs/<job_id>/`)."""

DEFAULT_BASE_NAME = "result"
"""Безопасное имя по умолчанию: детерминировано внутри Job и уникально."""

_EXTENSION_BY_FINAL: dict[FinalFormat, str] = {
    FinalFormat.PNG: "png",
    FinalFormat.JPEG: "jpeg",
    FinalFormat.WEBP: "webp",
}


class PillowArtifactStorage:
    """Реализация `ArtifactStorage` на локальной файловой системе через Pillow.

    `data_root` — сконфигурированный app-data root (`Settings.data_dir`); он
    нормализуется в абсолютный путь один раз, а managed-каталог создаётся только
    под `outputs/`. Пользовательские пути adapter не создаёт и не удаляет.

    Необязательный `logger` включает безопасные события `artifact_converted`,
    `artifact_saved`, `artifact_cleanup_failed` (C08c2a). Диагностика — best-effort:
    её сбой не превращает успешную публикацию в ошибку.
    """

    def __init__(self, *, data_root: Path, logger: EventLogger | None = None) -> None:
        self._data_root = Path(os.path.abspath(data_root))
        self._logger = logger

    def save(
        self,
        *,
        job_id: int,
        content: bytes,
        role: ArtifactRole = ArtifactRole.FINAL,
        final_format: FinalFormat | None = None,
        source_mime_type: str | None = None,
        output_dir: Path | None = None,
        base_name: str | None = None,
    ) -> Artifact:
        """Проверить байты, опубликовать файл no-clobber и вернуть метаданные файла.

        `source_mime_type` намеренно не используется для выбора формата: это
        недоверенная подсказка provider, а фактический MIME берётся из декодирования
        конечных байтов.
        """
        if type(job_id) is not int or job_id <= 0:
            raise ValueError("job_id must be a positive integer")
        prepared = self._prepare(content, role=role, final_format=final_format)
        directory, managed = self._select_directory(job_id=job_id, output_dir=output_dir)
        name = base_name if base_name is not None else DEFAULT_BASE_NAME
        extension = _EXTENSION_BY_FINAL[prepared.final_format]
        published = publish_output(prepared.data, directory, name, extension, managed=managed)

        local_path = published.path.relative_to(self._data_root) if managed else published.path
        artifact = Artifact(
            kind=ArtifactKind.IMAGE,
            role=role,
            local_path=local_path,
            mime_type=prepared.mime_type,
            size_bytes=published.size_bytes,
            sha256=published.sha256,
            metadata=self._metadata(prepared, ownership=published.ownership, published=published),
        )
        self._emit_publish_events(job_id=job_id, role=role, prepared=prepared, published=published)
        return artifact

    def exists(self, artifact: Artifact) -> bool:
        """Существует ли файл артефакта; история остаётся и после его удаления."""
        return self._resolve(artifact).exists()

    def resolve_path(self, artifact: Artifact) -> Path:
        """Вернуть фактический путь артефакта, отклонив выход за пределы data-root."""
        return self._resolve(artifact)

    def _prepare(
        self,
        content: bytes,
        *,
        role: ArtifactRole,
        final_format: FinalFormat | None,
    ) -> PreparedImage:
        """Проверить байты и выбрать конечный формат без скрытой перекодировки.

        `ORIGINAL` и FINAL без явного формата сохраняют исходный контейнер;
        FINAL с форматом конвертируется в него (включая no-op, когда формат уже
        совпадает).
        """
        if role is ArtifactRole.ORIGINAL or final_format is None:
            return prepare_image(content)
        return convert_image(content, final_format=final_format)

    def _select_directory(self, *, job_id: int, output_dir: Path | None) -> tuple[Path, bool]:
        """Выбрать каталог публикации и его принадлежность.

        Managed-каталог `outputs/<job_id>` создаётся здесь и только он; явный
        пользовательский каталог не создаётся и обязан уже существовать — это
        проверяет `publish_output`.
        """
        if output_dir is not None:
            return Path(output_dir), False
        managed_dir = self._data_root / OUTPUTS_DIRNAME / str(job_id)
        # Check every existing ancestor before mkdir can follow one outside data-root.
        # As in publish_output, concurrent hostile replacement is outside the threat model.
        for component in (managed_dir, *managed_dir.parents):
            if component.is_symlink() or component.is_junction():
                raise ValueError("Symlinked or junction output directory")
        managed_dir.mkdir(parents=True, exist_ok=True)
        return managed_dir, True

    def _resolve(self, artifact: Artifact) -> Path:
        """Разрешить сохранённый путь безопасно.

        Абсолютный путь — внешний (`user_output`) и возвращается как есть.
        Относительный managed-путь имеет форму `outputs/<positive-id>/<filename>`;
        переходы `..` и существующие файловые перенаправления отклоняются.
        """
        path = artifact.local_path
        if path is None:
            raise ValueError("Артефакт не имеет локального пути")
        if path.is_absolute():
            return path
        parts = path.parts
        if ".." in parts or len(parts) != 3 or parts[0] != OUTPUTS_DIRNAME:
            raise ValueError("Путь артефакта должен быть managed-путём внутри data-root")
        job_component = parts[1]
        if not (
            job_component.isascii()
            and job_component.isdecimal()
            and job_component[0] in "123456789"
        ):
            raise ValueError("Путь артефакта содержит недопустимый job_id")
        candidate = self._data_root / path
        for component in (candidate, *candidate.parents):
            if component.is_symlink() or component.is_junction():
                raise ValueError("Symlinked or junction managed artifact path")
        return candidate

    def _emit_publish_events(
        self,
        *,
        job_id: int,
        role: ArtifactRole,
        prepared: PreparedImage,
        published: PublishedOutput,
    ) -> None:
        """Best-effort события после фактической публикации файла.

        События пишутся только когда опубликованный файл уже существует. Сбой
        диагностики не должен превращать успешную публикацию в ошибку с orphan-
        файлом или подменять её, а сырой текст исключения в канал не переносится.
        Ремонт/идентификация артефакта остаётся в возвращаемом `Artifact`.
        """
        logger = self._logger
        if logger is None:
            return
        if prepared.converted:
            self._emit(
                lambda: log_artifact_converted(
                    logger,
                    job_id=job_id,
                    role=role,
                    source_format=prepared.source_format,
                    final_format=prepared.final_format,
                    width=prepared.width,
                    height=prepared.height,
                )
            )
        self._emit(
            lambda: log_artifact_saved(
                logger,
                job_id=job_id,
                role=role,
                final_format=prepared.final_format,
                size_bytes=published.size_bytes,
                width=prepared.width,
                height=prepared.height,
            )
        )
        if published.cleanup_failed_temp_path is not None:
            self._emit(lambda: log_artifact_cleanup_failed(logger, job_id=job_id, role=role))

    @staticmethod
    def _emit(event_call: Callable[[], None]) -> None:
        """Выполнить best-effort запись события, не подменяя результат публикации.

        Сбой обработчика диагностики проглатывается намеренно: файл уже опубликован,
        и ложный отказ `save` оставил бы orphan. Текст исключения логгеру не
        передаётся.
        """
        try:
            event_call()
        except Exception:
            return

    @staticmethod
    def _metadata(
        prepared: PreparedImage,
        *,
        ownership: str,
        published: PublishedOutput,
    ) -> dict[str, object]:
        """Метаданные конечных байтов без недоверенных и секретных значений.

        Сигнал `cleanup_failed_temp_path` публикации сохраняется как булев флаг, а
        не как путь: сырой временный путь не должен попадать в публичную
        диагностику/отчёты. Безопасное событие `artifact_cleanup_failed` пишется по
        этому флагу, но сам путь в запись не попадает.
        """
        return {
            "ownership": ownership,
            "width": prepared.width,
            "height": prepared.height,
            "converted": prepared.converted,
            "source_format": prepared.source_format.value,
            "final_format": prepared.final_format.value,
            "alpha_flattened": prepared.alpha_flattened,
            "temp_cleanup_failed": published.cleanup_failed_temp_path is not None,
        }
