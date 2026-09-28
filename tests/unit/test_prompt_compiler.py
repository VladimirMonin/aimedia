"""PromptCompiler: точный текст, порядок перемежающихся источников и snapshot.

Главное утверждение (`docs/plans/README.md`, E02): последовательность
`file A → inline B → file C → inline D` даёт ровно `A\\n\\nB\\n\\nC\\n\\nD` — без
перестановки, без склейки двух независимых списков и без переписывания текста.
Отдельно проверяется инвариант истории: изменение исходного файла после
компиляции не меняет сохранённый snapshot.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aimedia.application.prompts import (
    PROMPT_SOURCE_SEPARATOR,
    PromptCompiler,
    file_source,
    inline_source,
)
from aimedia.domain import (
    DomainErrorCode,
    InputFileNotFoundError,
    InvalidParameterValueError,
    PromptRequiredError,
    PromptSourceKind,
)

COMPILER = PromptCompiler()


def test_interleaved_sources_compile_to_exact_text(tmp_path: Path) -> None:
    """`file A → inline B → file C → inline D` даёт точно `A\\n\\nB\\n\\nC\\n\\nD`."""
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "c.md").write_text("C", encoding="utf-8")

    result = COMPILER.compile(
        [
            file_source(tmp_path / "a.md"),
            inline_source("B"),
            file_source(tmp_path / "c.md"),
            inline_source("D"),
        ]
    )

    assert result.compiled.text == "A\n\nB\n\nC\n\nD"
    assert result.compiled.source_count == 4


def test_compiled_prompt_hash_matches_exact_text() -> None:
    """SHA-256 фиксирует именно отправленный текст, а не его источник."""
    result = COMPILER.compile([inline_source("A"), inline_source("B")])

    assert result.compiled.sha256 == hashlib.sha256(b"A\n\nB").hexdigest()


def test_reversed_order_is_not_silently_normalized(tmp_path: Path) -> None:
    """Порядок источников сохраняется: обратный порядок даёт обратный текст."""
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "c.md").write_text("C", encoding="utf-8")

    forward = COMPILER.compile([file_source(tmp_path / "a.md"), inline_source("B")])
    reversed_ = COMPILER.compile([inline_source("B"), file_source(tmp_path / "a.md")])

    assert forward.compiled.text == "A\n\nB"
    assert reversed_.compiled.text == "B\n\nA"
    assert forward.compiled.text != reversed_.compiled.text


def test_source_positions_follow_cli_order(tmp_path: Path) -> None:
    """Позиции источников соответствуют порядку появления, а не типу."""
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    result = COMPILER.compile(
        [inline_source("B"), file_source(tmp_path / "a.md"), inline_source("D")]
    )

    assert [source.position for source in result.sources] == [0, 1, 2]
    assert [source.kind for source in result.sources] == [
        PromptSourceKind.INLINE,
        PromptSourceKind.FILE,
        PromptSourceKind.INLINE,
    ]
    assert result.sources[1].path == tmp_path / "a.md"


def test_file_text_is_not_rewritten() -> None:
    """Текст не переформатируется: значимые пробелы и переводы строк сохранены."""
    original = "  first line\n\nsecond\tline  \n"
    result = COMPILER.compile([inline_source(original)])

    assert result.compiled.text == original


def test_file_mutation_after_compilation_does_not_change_snapshot(tmp_path: Path) -> None:
    """Изменение исходного файла после подготовки не меняет сохранённый snapshot."""
    source_path = tmp_path / "character.md"
    source_path.write_text("Исходный персонаж", encoding="utf-8")

    prepared = COMPILER.compile([file_source(source_path)])
    snapshot_text = prepared.compiled.text
    snapshot_hash = prepared.compiled.sha256

    source_path.write_text("Полностью другой текст", encoding="utf-8")

    assert prepared.compiled.text == snapshot_text == "Исходный персонаж"
    assert prepared.compiled.sha256 == snapshot_hash
    assert prepared.sources[0].text == "Исходный персонаж"
    # Новая компиляция читает актуальный файл: snapshot не является кэшем.
    assert COMPILER.compile([file_source(source_path)]).compiled.text == "Полностью другой текст"


def test_whitespace_only_file_is_empty(tmp_path: Path) -> None:
    """Файл из одного whitespace считается пустым (`04-cli-contract.md`)."""
    (tmp_path / "blank.md").write_text("   \n\t\n", encoding="utf-8")
    result = COMPILER.compile([file_source(tmp_path / "blank.md"), inline_source("B")])

    assert result.compiled.text == "B"
    # Пустой источник остаётся в снимке истории со своей позицией.
    assert [source.position for source in result.sources] == [0, 1]


def test_all_sources_empty_requires_prompt(tmp_path: Path) -> None:
    """Если все источники пусты — PromptRequired, а не пустой prompt модели."""
    (tmp_path / "blank.md").write_text(" \n", encoding="utf-8")

    with pytest.raises(PromptRequiredError) as exc_info:
        COMPILER.compile([file_source(tmp_path / "blank.md"), inline_source("\t")])

    assert exc_info.value.code is DomainErrorCode.PROMPT_REQUIRED
    assert exc_info.value.details["source_count"] == 2


def test_empty_source_list_requires_prompt() -> None:
    with pytest.raises(PromptRequiredError):
        COMPILER.compile([])


def test_missing_prompt_file_is_pre_submit_error(tmp_path: Path) -> None:
    """Отсутствующий файл даёт понятную доменную ошибку до provider submit."""
    missing = tmp_path / "nope.md"

    with pytest.raises(InputFileNotFoundError) as exc_info:
        COMPILER.compile([file_source(missing), inline_source("B")])

    assert exc_info.value.code is DomainErrorCode.INPUT_FILE_NOT_FOUND
    assert missing.name in str(exc_info.value)


def test_non_utf8_prompt_file_is_reported(tmp_path: Path) -> None:
    """Файл, не декодируемый как UTF-8, не попадает в prompt молча."""
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"\xff\xfe\x00invalid-utf8")

    with pytest.raises(InvalidParameterValueError) as exc_info:
        COMPILER.compile([file_source(broken)])

    assert exc_info.value.code is DomainErrorCode.INVALID_PARAMETER_VALUE
    assert exc_info.value.details["parameter"] == "--prompt-file"


def test_utf8_prompt_file_decodes_without_bom_mangling(tmp_path: Path) -> None:
    """Кириллица и эмодзи в UTF-8 проходят без искажения."""
    source = tmp_path / "cyrillic.md"
    source.write_text("Лабораторный робот 🤖", encoding="utf-8")

    result = COMPILER.compile([file_source(source)])

    assert result.compiled.text == "Лабораторный робот 🤖"


def test_batch_items_do_not_share_prompt_state(tmp_path: Path) -> None:
    """Prompt-источники разных Jobs не смешиваются: каждый compile изолирован."""
    (tmp_path / "one.md").write_text("Prompt-1", encoding="utf-8")
    (tmp_path / "two.md").write_text("Prompt-2", encoding="utf-8")

    first = COMPILER.compile([file_source(tmp_path / "one.md")])
    second = COMPILER.compile([file_source(tmp_path / "two.md")])

    assert first.compiled.text == "Prompt-1"
    assert second.compiled.text == "Prompt-2"
    assert first.sources[0].text == "Prompt-1"
    assert second.sources[0].text == "Prompt-2"
    assert first.sources != second.sources


def test_separator_is_two_newlines() -> None:
    assert PROMPT_SOURCE_SEPARATOR == "\n\n"
    assert PromptCompiler.separator == "\n\n"


def test_source_request_rejects_inconsistent_payload() -> None:
    """Неконсистентный запрос источника отклоняется до чтения файлов."""
    from aimedia.application.prompts import PromptSourceRequest

    with pytest.raises(ValueError, match="обязан нести текст"):
        PromptSourceRequest(kind=PromptSourceKind.INLINE)
    with pytest.raises(ValueError, match="обязан нести путь"):
        PromptSourceRequest(kind=PromptSourceKind.FILE)
    with pytest.raises(ValueError, match="не может нести путь"):
        PromptSourceRequest(kind=PromptSourceKind.INLINE, text="A", path=Path("a.md"))
    with pytest.raises(ValueError, match="не может нести готовый текст"):
        PromptSourceRequest(kind=PromptSourceKind.FILE, text="A", path=Path("a.md"))
