"""Строгий data-only загрузчик Model Registry не исполняет Python-конструкторы.

Проверяется именно безопасность разбора YAML: явные теги `!!python/...`,
`%TAG`-директивы и попытки исполнить callable отклоняются типизированной ошибкой
до построения объектов (`docs/plans/README.md`, E03; тест
`tests/security/test_registry_data_only.py`).

Fixtures синтетические: они не объявляют ни одной реальной модели, API ID или
лимита.
"""

from __future__ import annotations

import pytest

from aimedia.registry import UnsafeYamlTagError, parse_model_record
from aimedia.registry.errors import DuplicateYamlKeyError, InvalidRegistryFileError


def test_python_object_apply_tag_is_rejected() -> None:
    """`!!python/object/apply` не исполняется и распознаётся как запрещённый тег."""
    text = "schema_version: 1\nid: ok\nevil: !!python/object/apply:os.system ['echo hi']\n"
    with pytest.raises(UnsafeYamlTagError) as excinfo:
        parse_model_record(text)
    assert "python/object/apply" in excinfo.value.tag
    assert excinfo.value.code == "UNSAFE_YAML_TAG"


def test_python_name_tag_is_rejected() -> None:
    """`!!python/name` (импорт callable без вызова) тоже запрещён."""
    text = "schema_version: 1\nid: ok\nhandler: !!python/name:os.system\n"
    with pytest.raises(UnsafeYamlTagError):
        parse_model_record(text)


@pytest.mark.parametrize(
    "tag",
    ["!!omap", "!!set", "!!binary", "!!pairs"],
)
def test_non_scalar_core_tags_are_rejected(tag: str) -> None:
    """Разрешены только скаляры/seq/map ядра: агрегатные теги не принимаются."""
    text = f"schema_version: 1\nid: ok\nvalue: {tag} [a, b]\n"
    with pytest.raises(UnsafeYamlTagError):
        parse_model_record(text)


def test_tag_directive_is_rejected() -> None:
    """`%TAG` позволяет подменить префикс и обойти allowlist, поэтому запрещён."""
    text = "%TAG !e! tag:example.com,2020:\n---\nschema_version: 1\nid: ok\nv: !e!thing x\n"
    with pytest.raises(UnsafeYamlTagError):
        parse_model_record(text)


def test_duplicate_yaml_key_is_rejected() -> None:
    """Один ключ дважды в одном mapping — явная ошибка, а не последнее значение."""
    text = "schema_version: 1\nid: a\nname: First\nname: Second\nfamily: image\nstatus: active\n"
    with pytest.raises(DuplicateYamlKeyError) as excinfo:
        parse_model_record(text)
    assert excinfo.value.key == "name"
    assert excinfo.value.code == "DUPLICATE_YAML_KEY"


def test_nested_duplicate_key_is_rejected() -> None:
    """Дубликат внутри вложенного mapping также обнаруживается."""
    text = (
        "schema_version: 1\n"
        "id: a\n"
        "name: A\n"
        "family: image\n"
        "status: active\n"
        "inputs:\n"
        "  images:\n"
        "    max: 4\n"
        "    max: 2\n"
    )
    with pytest.raises(DuplicateYamlKeyError):
        parse_model_record(text)


def test_multiple_documents_are_rejected() -> None:
    """Один файл — одна модель: второй документ отклоняется."""
    text = (
        "schema_version: 1\nid: a\nname: A\nfamily: image\nstatus: active\n"
        "---\n"
        "schema_version: 1\nid: b\nname: B\nfamily: image\nstatus: active\n"
    )
    with pytest.raises(InvalidRegistryFileError):
        parse_model_record(text)


@pytest.mark.parametrize("second_document", ["---\n", "---\nnull\n"])
def test_empty_second_document_is_rejected(second_document: str) -> None:
    text = "schema_version: 1\nid: a\nname: A\nfamily: image\nstatus: active\n"
    text += second_document
    with pytest.raises(InvalidRegistryFileError, match="один документ, найдено 2") as excinfo:
        parse_model_record(text)
    assert excinfo.value.code == "INVALID_REGISTRY_FILE"


def test_non_mapping_document_is_rejected() -> None:
    """Документ-список не является записью Registry."""
    with pytest.raises(InvalidRegistryFileError):
        parse_model_record("- schema_version\n- 1\n")


def test_safe_scalar_tags_still_load() -> None:
    """Allowlist не ломает обычный data-only документ."""
    text = (
        "schema_version: 1\n"
        "id: synthetic-one\n"
        "name: Synthetic One\n"
        "family: image\n"
        "status: active\n"
        "capabilities:\n"
        "  text_to_image: true\n"
    )
    record = parse_model_record(text)
    assert record.model_id == "synthetic-one"
