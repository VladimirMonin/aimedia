"""Компиляция prompt из одного упорядоченного списка источников.

Контракт (решение baseline D04, `04-cli-contract.md`, «Порядок объединения»):
`--prompt` и `--prompt-file` образуют **один** список в порядке появления в CLI.
Два отдельных списка с последующей склейкой не реализуют контракт, поэтому API
принимает `Sequence[PromptSourceRequest]`, где позиция источника уже зафиксирована.

Текст не переписывается: снимок каждого источника сохраняется как есть после
декодирования UTF-8, а compiled prompt — это ровно `PROMPT_SOURCE_SEPARATOR.join`
снимков непустых источников. Изменение файла на диске после компиляции не меняет
`PromptPreparation`: история показывает текст, который был реально подготовлен.

Проверки до submit (`08-job-execution.md`, «Pre-submit validation») выражаются
существующими доменными ошибками: отсутствующий/нечитаемый файл —
`InputFileNotFoundError`, файл не UTF-8 — `InvalidParameterValueError`, все
источники пусты — `PromptRequiredError`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aimedia.domain.errors import (
    DomainError,
    InputFileNotFoundError,
    InvalidParameterValueError,
    PromptRequiredError,
)
from aimedia.domain.inputs import CompiledPrompt, PromptSource, PromptSourceKind
from aimedia.logging import EventLogger

PROMPT_SOURCE_SEPARATOR = "\n\n"
"""Разделитель между источниками prompt (решение baseline D04)."""

_ENCODING = "utf-8"


@dataclass(frozen=True, slots=True)
class PromptSourceRequest:
    """Один источник prompt до чтения: строка CLI или путь к файлу.

    Позиция здесь не хранится: порядок задаёт сама последовательность запросов,
    а `PromptCompiler` присваивает позиции, поэтому перестановка источников не
    может быть незаметной.
    """

    kind: PromptSourceKind
    text: str | None = None
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.kind is PromptSourceKind.INLINE:
            if self.text is None:
                raise ValueError("inline-источник обязан нести текст")
            if self.path is not None:
                raise ValueError("inline-источник не может нести путь")
        else:
            if self.path is None:
                raise ValueError("файловый источник обязан нести путь")
            if self.text is not None:
                raise ValueError("файловый источник не может нести готовый текст")


def inline_source(text: str) -> PromptSourceRequest:
    """Источник `--prompt`: готовая строка CLI."""
    return PromptSourceRequest(kind=PromptSourceKind.INLINE, text=text)


def file_source(path: str | Path) -> PromptSourceRequest:
    """Источник `--prompt-file`: путь, который будет прочитан при компиляции."""
    return PromptSourceRequest(kind=PromptSourceKind.FILE, path=Path(path))


@dataclass(frozen=True, slots=True)
class PromptPreparation:
    """Снимок источников и точный compiled prompt одного Job.

    `sources` сохраняет все прочитанные источники с их позициями (включая пустые),
    `compiled` — текст, который уходит модели. Batch-Job не делит это состояние с
    соседним Job: каждый вызов `PromptCompiler.compile` создаёт собственную
    неизменяемую подготовку.
    """

    sources: tuple[PromptSource, ...]
    compiled: CompiledPrompt


class PromptCompiler:
    """Собирает упорядоченные источники в один compiled prompt.

    Компилятор не знает CLI, модели и provider (`02-system-architecture.md`):
    на вход приходит уже упорядоченная последовательность источников, на выходе —
    неизменяемый снимок. Логгер необязателен: чистые преобразования не обязаны
    логировать, а диагностика остаётся на границе application.
    """

    separator = PROMPT_SOURCE_SEPARATOR

    def compile(
        self,
        requests: Sequence[PromptSourceRequest],
        *,
        logger: EventLogger | None = None,
    ) -> PromptPreparation:
        """Прочитать источники в заданном порядке и собрать compiled prompt."""
        try:
            prepared = self._compile(requests)
        except DomainError as exc:
            _log_validation_failed(logger, exc)
            raise
        _log_prompt_compiled(logger, prepared)
        return prepared

    def _compile(self, requests: Sequence[PromptSourceRequest]) -> PromptPreparation:
        sources = tuple(
            PromptSource(
                kind=request.kind,
                text=self._text_of(request, position),
                position=position,
                path=request.path,
            )
            for position, request in enumerate(requests)
        )

        # Файл только из whitespace считается пустым (`04-cli-contract.md`,
        # «Пустые источники»), поэтому пустые источники не создают двойных
        # разделителей и не участвуют в счётчике.
        contributing = [source.text for source in sources if source.text.strip()]
        if not contributing:
            raise PromptRequiredError(
                "Пустой prompt: все источники пусты.",
                details={"source_count": len(sources)},
            )

        text = self.separator.join(contributing)
        return PromptPreparation(
            sources=sources,
            compiled=CompiledPrompt(
                text=text,
                source_count=len(contributing),
                sha256=hashlib.sha256(text.encode(_ENCODING)).hexdigest(),
            ),
        )

    def _text_of(self, request: PromptSourceRequest, position: int) -> str:
        if request.kind is PromptSourceKind.INLINE:
            assert request.text is not None  # гарантировано PromptSourceRequest
            return request.text
        assert request.path is not None  # гарантировано PromptSourceRequest
        return _read_prompt_file(request.path, position)


def _read_prompt_file(path: Path, position: int) -> str:
    """Прочитать prompt-файл как UTF-8, не меняя его текст."""
    details: dict[str, object] = {"path": path.as_posix(), "position": position}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputFileNotFoundError(
            f"Не удалось прочитать prompt-файл {path}: {exc.strerror or exc}.",
            details=details,
        ) from exc
    try:
        return raw.decode(_ENCODING)
    except UnicodeDecodeError as exc:
        raise InvalidParameterValueError(
            f"Prompt-файл {path} не декодируется как {_ENCODING}; "
            "в v0.1 текстовые prompt-файлы ожидаются в UTF-8.",
            details={**details, "parameter": "--prompt-file", "encoding": _ENCODING},
        ) from exc


def _log_validation_failed(logger: EventLogger | None, exc: DomainError) -> None:
    """Записать отказ подготовки до submit без текста prompt и содержимого файлов."""
    if logger is None:
        return
    logger.event(
        "validation_failed",
        level="WARNING",
        details={"code": exc.code.value},
    )


def _log_prompt_compiled(logger: EventLogger | None, prepared: PromptPreparation) -> None:
    """Записать компиляцию prompt: числа и длина, но не сам текст prompt."""
    if logger is None:
        return
    logger.event(
        "prompt_compiled",
        details={
            "source_count": prepared.compiled.source_count,
            "input_source_count": len(prepared.sources),
            "length_chars": len(prepared.compiled.text),
        },
    )
