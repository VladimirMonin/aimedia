"""Строгий загрузчик Model Registry: схема, уникальность и типизированные ошибки.

Проверяемые утверждения этапа E03 (`docs/plans/README.md`): дубликаты ключей/ID/
alias, неизвестная версия схемы и `default` вне enum дают явную ошибку; запись
модели разбирается в типизированный DTO с сохранением провенанса; неизвестное
ограничение остаётся `None`, а не превращается в придуманное число.

Все YAML здесь синтетические (fixtures); реальные API ID и лимиты не выдумываются.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aimedia.registry import (
    DuplicateAliasError,
    DuplicateModelIdError,
    Family,
    InvalidModelRecordError,
    InvalidRegistryFileError,
    ModelStatus,
    ParameterType,
    UnsupportedSchemaVersionError,
    builtin_registry_dir,
    load_builtin_registry,
    load_model_file,
    load_registry,
    load_registry_root,
    parse_model_record,
    registry_files,
)

IMAGE_MODEL_YAML = """\
schema_version: 1

id: synthetic-image-one
name: Synthetic Image One

family: image
status: active

aliases:
  - synthetic-one
  - syn-image-1

capabilities:
  text_to_image: true
  image_to_image:
    supported: true
    max: 4

inputs:
  prompt:
    required: true
    max_chars: 20000
  images:
    min: 0
    max: 4
    formats:
      - png
      - jpeg
      - webp

outputs:
  images:
    min: 1
    max: 4

parameters:
  resolution:
    type: enum
    values:
      - 1K
      - 2K
    default: 2K

  seed:
    type: integer
    min: 0
    max: 2147483647

providers:
  polza:
    remote_model_id: synthetic/remote-model-alpha
    parameter_overrides:
      resolution:
        values:
          - 1K

verification:
  checked_at: 2026-09-28
  source: provider_docs

verified_at: 2026-09-28

docs:
  overview: models/synthetic-image-one.md
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_full_record_parses_into_typed_dto() -> None:
    """Полный документ разбирается в DTO с ожидаемыми значениями и типами."""
    record = parse_model_record(IMAGE_MODEL_YAML)

    assert record.schema_version == 1
    assert record.model_id == "synthetic-image-one"
    assert record.name == "Synthetic Image One"
    assert record.family is Family.IMAGE
    assert record.status is ModelStatus.ACTIVE
    assert record.aliases == ("synthetic-one", "syn-image-1")

    reference = record.capabilities["image_to_image"]
    assert not isinstance(reference, bool)
    assert reference.supported is True
    assert reference.max == 4
    assert record.capabilities["text_to_image"] is True

    assert record.inputs["images"].formats == ("png", "jpeg", "webp")
    assert record.inputs["prompt"].max_chars == 20000
    assert record.outputs["images"].min == 1

    resolution = record.parameters["resolution"]
    assert resolution.type is ParameterType.ENUM
    assert resolution.values == ("1K", "2K")
    assert resolution.default == "2K"

    binding = record.providers["polza"]
    assert binding.remote_model_id == "synthetic/remote-model-alpha"
    assert binding.parameter_overrides["resolution"].values == ("1K",)

    assert record.verification is not None
    assert record.verification.checked_at == date(2026, 9, 28)
    assert record.verification.source == "provider_docs"
    assert record.verified_at == date(2026, 9, 28)
    assert record.docs is not None
    assert record.docs.overview == "models/synthetic-image-one.md"


def test_record_is_immutable() -> None:
    """DTO неизменяем: registry-запись не переписывается после загрузки."""
    record = parse_model_record(IMAGE_MODEL_YAML)
    with pytest.raises(ValidationError):
        record.model_id = "other"  # type: ignore[misc]


def test_unknown_limits_stay_none() -> None:
    """Неизвестное ограничение остаётся `None`, а не придуманным числом."""
    text = (
        "schema_version: 1\n"
        "id: synthetic-min\n"
        "name: Synthetic Min\n"
        "family: image\n"
        "status: experimental\n"
        "inputs:\n"
        "  images:\n"
        "    max: null\n"
    )
    record = parse_model_record(text)
    images = record.inputs["images"]
    assert images.max is None
    assert images.min is None
    assert record.verified_at is None
    assert record.verification is None


def test_yaml_id_maps_to_model_id_field() -> None:
    """YAML-ключ `id` нормализуется в DTO-поле `model_id` (baseline D06)."""
    text = "schema_version: 1\nid: synthetic-map\nname: N\nfamily: image\nstatus: active\n"
    assert parse_model_record(text).model_id == "synthetic-map"


def test_model_id_and_id_together_are_rejected() -> None:
    """Два написания одного концепта одновременно — конфликт, а не приоритет."""
    text = (
        "schema_version: 1\n"
        "id: synthetic-a\n"
        "model_id: synthetic-b\n"
        "name: N\n"
        "family: image\n"
        "status: active\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_missing_schema_version_is_rejected() -> None:
    """Запись без schema_version не принимается."""
    text = "id: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_unknown_schema_version_is_rejected() -> None:
    """Неизвестная версия схемы — явная ошибка со списком поддерживаемых."""
    text = "schema_version: 99\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
    with pytest.raises(UnsupportedSchemaVersionError) as excinfo:
        parse_model_record(text)
    assert excinfo.value.version == 99
    assert excinfo.value.details["supported"] == [1]


def test_non_integer_schema_version_is_rejected() -> None:
    """Строковая версия не проходит: версия — целое число, не «1»."""
    text = "schema_version: '1'\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
    with pytest.raises(UnsupportedSchemaVersionError):
        parse_model_record(text)


def test_default_outside_enum_is_rejected() -> None:
    """`default` вне списка enum values останавливает загрузку."""
    text = (
        "schema_version: 1\n"
        "id: synthetic-a\n"
        "name: N\n"
        "family: image\n"
        "status: active\n"
        "parameters:\n"
        "  resolution:\n"
        "    type: enum\n"
        "    values:\n"
        "      - 1K\n"
        "      - 2K\n"
        "    default: 8K\n"
    )
    with pytest.raises(InvalidModelRecordError) as excinfo:
        parse_model_record(text)
    assert any("default" in error["msg"] for error in excinfo.value.errors)


def test_enum_without_values_is_rejected() -> None:
    """Enum-параметр без `values` некорректен."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "parameters:\n  resolution:\n    type: enum\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_default_wrong_type_is_rejected() -> None:
    """`default` несоответствующего типа (строка для integer) отклоняется."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "parameters:\n  seed:\n    type: integer\n    default: many\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_unknown_field_is_rejected() -> None:
    """Лишнее/опечатанное поле отклоняется (`extra="forbid"`)."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "capabilites:\n  text_to_image: true\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_unknown_parameter_type_is_rejected() -> None:
    """Тип параметра вне базового набора не принимается."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "parameters:\n  x:\n    type: object\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_unknown_family_and_status_are_rejected() -> None:
    """Family/status вне допустимого enum отклоняются."""
    bad_family = "schema_version: 1\nid: a\nname: N\nfamily: music\nstatus: active\n"
    bad_status = "schema_version: 1\nid: a\nname: N\nfamily: image\nstatus: maybe\n"
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(bad_family)
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(bad_status)


def test_uppercase_or_dotted_id_is_rejected() -> None:
    """Канонический ID не в lowercase-kebab-case отклоняется."""
    for bad_id in ("Synthetic_Image", "qwen/image", "Synthetic"):
        text = f"schema_version: 1\nid: {bad_id}\nname: N\nfamily: image\nstatus: active\n"
        with pytest.raises(InvalidModelRecordError):
            parse_model_record(text)


def test_provider_binding_rejects_provider_config_keys() -> None:
    """Binding не содержит provider config: API key/base URL запрещены схемой."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "providers:\n  polza:\n    remote_model_id: r\n    api_key: secret\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_parameter_map_is_not_part_of_schema() -> None:
    """`parameter_map` неактивен в v0.1 (baseline D07) и отклоняется схемой."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "providers:\n  polza:\n    remote_model_id: r\n"
        "    parameter_map:\n      resolution: image_resolution\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_alias_equal_to_own_id_is_rejected() -> None:
    """Alias не может совпадать с каноническим ID той же записи."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "aliases:\n  - synthetic-a\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def test_duplicate_alias_within_record_is_rejected() -> None:
    """Повтор alias внутри одной записи отклоняется."""
    text = (
        "schema_version: 1\nid: synthetic-a\nname: N\nfamily: image\nstatus: active\n"
        "aliases:\n  - dup\n  - dup\n"
    )
    with pytest.raises(InvalidModelRecordError):
        parse_model_record(text)


def _base(model_id: str, *, aliases: tuple[str, ...] = ()) -> str:
    alias_block = "".join(f"  - {alias}\n" for alias in aliases)
    aliases_yaml = f"aliases:\n{alias_block}" if aliases else ""
    return (
        f"schema_version: 1\nid: {model_id}\nname: {model_id}\n"
        f"family: image\nstatus: active\n{aliases_yaml}"
    )


def test_duplicate_canonical_id_across_files_is_rejected(tmp_path: Path) -> None:
    """Один канонический ID в двух файлах делает Registry невалидным."""
    first = _write(tmp_path / "a.yaml", _base("synthetic-dup"))
    second = _write(tmp_path / "b.yaml", _base("synthetic-dup"))
    with pytest.raises(DuplicateModelIdError) as excinfo:
        load_registry([first, second])
    assert excinfo.value.model_id == "synthetic-dup"


def test_duplicate_alias_across_files_is_rejected(tmp_path: Path) -> None:
    """Alias не может принадлежать двум моделям."""
    first = _write(tmp_path / "a.yaml", _base("synthetic-a", aliases=("shared",)))
    second = _write(tmp_path / "b.yaml", _base("synthetic-b", aliases=("shared",)))
    with pytest.raises(DuplicateAliasError) as excinfo:
        load_registry([first, second])
    assert excinfo.value.alias == "shared"


def test_alias_conflicting_with_canonical_id_is_rejected(tmp_path: Path) -> None:
    """Alias, совпадающий с каноническим ID другой модели, отклоняется."""
    first = _write(tmp_path / "a.yaml", _base("synthetic-a"))
    second = _write(tmp_path / "b.yaml", _base("synthetic-b", aliases=("synthetic-a",)))
    with pytest.raises(DuplicateAliasError):
        load_registry([first, second])


def test_registry_ordering_is_stable(tmp_path: Path) -> None:
    """Порядок записей не зависит от порядка переданных путей."""
    first = _write(tmp_path / "b.yaml", _base("synthetic-b"))
    second = _write(tmp_path / "a.yaml", _base("synthetic-a"))
    records = load_registry([first, second])
    assert [record.model_id for record in records] == ["synthetic-a", "synthetic-b"]


def test_load_model_file_and_root(tmp_path: Path) -> None:
    """Загрузка с диска и обход каталога возвращают те же записи."""
    _write(tmp_path / "models" / "one.yaml", _base("synthetic-one"))
    _write(tmp_path / "models" / "two.yaml", _base("synthetic-two"))

    assert [record.model_id for record in load_registry_root(tmp_path)] == [
        "synthetic-one",
        "synthetic-two",
    ]
    single = load_model_file(tmp_path / "models" / "one.yaml")
    assert single.model_id == "synthetic-one"
    assert registry_files(tmp_path) == (
        tmp_path / "models" / "one.yaml",
        tmp_path / "models" / "two.yaml",
    )


def test_load_registry_root_missing_directory_is_empty(tmp_path: Path) -> None:
    """Отсутствующий каталог даёт пустой набор, а не исключение."""
    assert load_registry_root(tmp_path / "absent") == ()


def test_builtin_catalog_is_documented_experimental_not_live_verified() -> None:
    """Only official catalog bindings, with conservative Guide reference limit."""
    records = load_builtin_registry()
    assert len(registry_files(builtin_registry_dir())) == len(records) == 3
    assert {record.providers["polza"].remote_model_id for record in records} == {
        "qwen/image-2.1",
        "google/gemini-3.1-flash-image-preview",
        "openai/gpt-5.4-image-2@mie",
    }
    for record in records:
        assert record.status.value == "experimental"
        assert record.verification is not None and record.verified_at is None
        if record.model_id != "gpt-5-4-image-2-mie":
            assert "polza.ai/api/v1/models/catalog" in record.verification.source
            assert "max_images" not in record.parameters
        else:
            assert "polza.ai/docs/gaidy/gpt-5-4-image-2.md" in record.verification.source
            assert "polza.ai/models/openai/gpt-5.4-image-2.md" in record.verification.source
            assert record.parameters["max_images"].max == 4
            assert record.pricing.by_resolution == {"1K": Decimal("4")}
            assert record.pricing.unit_parameter is None
    gemini = next(record for record in records if record.model_id.startswith("gemini"))
    assert gemini.inputs["images"].max == 8


def test_builtin_registry_dir_is_resolved_from_package_resource() -> None:
    """Путь берётся из package resource, а не из относительного `Path('./registry')`."""
    assert builtin_registry_dir().name == "data"


def test_invalid_yaml_syntax_is_rejected() -> None:
    """Синтаксически некорректный YAML даёт типизированную ошибку файла."""
    with pytest.raises(InvalidRegistryFileError):
        parse_model_record("schema_version: 1\nid: [unclosed\n")
