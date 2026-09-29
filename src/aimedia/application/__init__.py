"""Прикладной слой aimedia: сценарии подготовки входов и компиляции prompt.

Слой координирует данные, но не знает деталей HTTP, ORM, CLI и YAML
(`docs/plans/02-system-architecture.md`). C04 реализует здесь две операции
подготовки, вызываемые до любого provider submit:

- компиляция prompt из одного упорядоченного списка источников
  (`aimedia.application.prompts.compile`);
- подготовка reference images: порядок, фактический MIME по содержимому, размер
  и SHA-256 (`aimedia.application.inputs.prepare`).

C08c2b добавляет финализацию image Job: публикацию финального artifact через порт
`ArtifactStorage` и запись `completed` в историю через `JobRepository`. Если
завершение истории после публикации не подтверждено, поднимается
`ArtifactHistoryWriteError` с опубликованным `Artifact`; статус БД может быть
уже completed и требует сверки (`aimedia.application.artifact_finalization`).

Домен остаётся IO-free: чтение файлов выполняется только в этом слое, а результат
подготовки представлен доменными DTO `PromptSource`, `CompiledPrompt` и `InputRef`.
"""

from __future__ import annotations

from aimedia.application.artifact_finalization import (
    ArtifactHistoryWriteError,
    finalize_image_artifact,
)

__all__ = ["ArtifactHistoryWriteError", "finalize_image_artifact"]
