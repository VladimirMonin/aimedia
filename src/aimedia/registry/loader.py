"""Strict data-only загрузчик Model Registry.

Загрузчик читает YAML строго как данные: он не исполняет Python-конструкторы,
не разрешает неизвестные теги и директивы и не допускает дублирующихся ключей.
Любая проблема становится типизированной ошибкой из
:mod:`aimedia.registry.errors`, а не «тихим» permissive-разбором
(`docs/plans/06-model-registry.md`, `docs/plans/README.md`, E03).

Соответствие YAML ↔ DTO:

- ключ `id` документа нормализуется в поле `model_id` — канонический ID
  (baseline D06: `model_id` / `remote_model_id`);
- один файл содержит ровно один YAML-документ: несколько документов в файле
  отклоняются, потому что одна запись описывает одну логическую модель;
- `providers.<name>.parameter_overrides` остаются декларативными: слияние с
  базовой моделью выполняет effective-resolver (C05b), а не загрузчик.

Загрузка полностью offline и не зависит от provider adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError
from yaml.nodes import MappingNode, Node, SequenceNode
from yaml.tokens import DirectiveToken

from aimedia.registry.errors import (
    DuplicateAliasError,
    DuplicateModelIdError,
    DuplicateYamlKeyError,
    InvalidModelRecordError,
    InvalidRegistryFileError,
    UnsafeYamlTagError,
    UnsupportedSchemaVersionError,
)
from aimedia.registry.models import ModelRecord

# Версии схемы, которые умеет читать этот загрузчик. Неизвестная версия — явная
# ошибка, а не попытка «угадать формат» (`06-model-registry.md`, Schema Version).
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

# Разрешённые YAML-теги: только скалярные типы и коллекции ядра data-схемы.
# Явный allowlist отсекает `!!python/...`, `!!omap`, `!!set`, `!!binary` и любые
# теги, которые могли бы привести к созданию не-скалярных объектов.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:timestamp",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:map",
    }
)

# Ключ-алиас документа: в YAML запись использует `id`, DTO хранит `model_id`.
_YAML_ID_KEY = "id"
_MODEL_ID_FIELD = "model_id"
_SCHEMA_VERSION_KEY = "schema_version"


class _DataOnlyLoader(yaml.SafeLoader):
    """SafeLoader с запретом дублирующихся ключей и небезопасных тегов."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise InvalidRegistryFileError("ожидался mapping-узел")
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
            try:
                duplicate = key in mapping
            except TypeError as exc:  # нехешируемый ключ (список/mapping)
                raise InvalidRegistryFileError(
                    "ключ YAML должен быть скаляром", details={"key_type": type(key).__name__}
                ) from exc
            if duplicate:
                line = key_node.start_mark.line + 1
                raise DuplicateYamlKeyError(str(key), line=line)
            mapping[key] = self.construct_object(  # type: ignore[no-untyped-call]
                value_node, deep=deep
            )
        return mapping


def _check_directives(text: str, *, path: Path | None) -> None:
    """Отклонить YAML-директивы, кроме явного `%YAML 1.1`/`%YAML 1.2`.

    `%TAG` подменяет префиксы тегов и тем самым обходит allowlist, поэтому
    запрещён целиком.
    """
    try:
        tokens = list(yaml.scan(text))
    except yaml.YAMLError as exc:  # pragma: no cover - защита от нечитаемого потока
        raise InvalidRegistryFileError(f"не удалось разобрать YAML: {exc}", path=path) from exc
    for token in tokens:
        if not isinstance(token, DirectiveToken):
            continue
        if token.name != "YAML" or token.value not in ((1, 1), (1, 2)):
            raise UnsafeYamlTagError(
                f"%{token.name} {token.value}", path=path, line=token.start_mark.line + 1
            )


def _check_tags(node: Node, *, path: Path | None) -> None:
    """Проверить все теги документа против allowlist до конструирования объектов."""
    if node.tag not in _ALLOWED_TAGS:
        raise UnsafeYamlTagError(node.tag, path=path, line=node.start_mark.line + 1)
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            _check_tags(key_node, path=path)
            _check_tags(value_node, path=path)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            _check_tags(child, path=path)


def _load_single_document(text: str, *, path: Path | None) -> Any:
    """Разобрать ровно один data-only YAML-документ."""
    if not isinstance(text, str):
        raise InvalidRegistryFileError("источник Registry должен быть текстом", path=path)
    _check_directives(text, path=path)
    try:
        documents = list(yaml.load_all(text, Loader=_DataOnlyLoader))
    except DuplicateYamlKeyError as exc:
        raise DuplicateYamlKeyError(exc.key, path=path, line=exc.details.get("line")) from exc
    except UnsafeYamlTagError as exc:
        raise UnsafeYamlTagError(exc.tag, path=path, line=exc.details.get("line")) from exc
    except yaml.YAMLError as exc:
        raise InvalidRegistryFileError(f"некорректный YAML: {exc}", path=path) from exc
    if len(documents) != 1:
        raise InvalidRegistryFileError(
            f"файл Registry должен содержать один документ, найдено {len(documents)}",
            path=path,
        )
    if not isinstance(documents[0], dict):
        raise InvalidRegistryFileError("документ Registry должен быть mapping", path=path)
    return documents[0]


def _validate_tags(text: str, *, path: Path | None) -> None:
    """Проверить теги по скомпонованному дереву до конструирования объектов.

    `yaml.compose_all` даёт теги всех узлов (явных и неявно разрешённых); вызов
    идёт до `yaml.load_all`, чтобы запрещённый тег не успел создать объект.
    """
    try:
        for node in yaml.compose_all(text, Loader=_DataOnlyLoader):
            if node is not None:
                _check_tags(node, path=path)
    except yaml.YAMLError as exc:
        raise InvalidRegistryFileError(f"некорректный YAML: {exc}", path=path) from exc


def _normalise_id_key(raw: dict[str, Any], *, path: Path | None) -> dict[str, Any]:
    """Привести YAML-ключ `id` к DTO-полю `model_id`.

    Одновременное присутствие `id` и `model_id` — конфликт двух написаний одного
    концепта, а не приоритет одного над другим.
    """
    if _YAML_ID_KEY not in raw:
        return raw
    if _MODEL_ID_FIELD in raw:
        raise InvalidModelRecordError(
            path=path,
            errors=[{"loc": [_YAML_ID_KEY], "msg": "`id` и `model_id` нельзя задавать вместе"}],
        )
    normalised = dict(raw)
    normalised[_MODEL_ID_FIELD] = normalised.pop(_YAML_ID_KEY)
    return normalised


def parse_model_record(text: str, *, path: Path | None = None) -> ModelRecord:
    """Разобрать один YAML-документ Registry в типизированную запись модели.

    Порядок проверок: директивы → теги → дубликаты ключей → версия схемы →
    схема Pydantic. Каждая ветка даёт свою типизированную ошибку.
    """
    _validate_tags(text, path=path)
    raw = _load_single_document(text, path=path)
    raw = _normalise_id_key(raw, path=path)

    version = raw.get(_SCHEMA_VERSION_KEY)
    if version is None:
        raise InvalidModelRecordError(
            path=path, errors=[{"loc": [_SCHEMA_VERSION_KEY], "msg": "schema_version обязателен"}]
        )
    if isinstance(version, bool) or not isinstance(version, int):
        raise UnsupportedSchemaVersionError(version, supported=SUPPORTED_SCHEMA_VERSIONS, path=path)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersionError(version, supported=SUPPORTED_SCHEMA_VERSIONS, path=path)

    try:
        return ModelRecord.model_validate(raw)
    except PydanticValidationError as exc:
        raise InvalidModelRecordError(path=path, errors=_error_details(exc)) from exc


def _error_details(exc: PydanticValidationError) -> list[dict[str, Any]]:
    """Свести ошибки Pydantic к переносимому виду без внутренних объектов."""
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        details.append(
            {
                "loc": [str(part) for part in error["loc"]],
                "msg": error["msg"],
                "type": error["type"],
            }
        )
    return details


def load_model_file(path: Path) -> ModelRecord:
    """Прочитать один файл Registry с диска."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidRegistryFileError(f"не удалось прочитать файл: {exc}", path=path) from exc
    return parse_model_record(text, path=path)


def load_registry(files: Iterable[Path]) -> tuple[ModelRecord, ...]:
    """Загрузить набор файлов и проверить уникальность ID и alias.

    Canonical ID уникален между файлами; alias не может повторяться и не может
    совпадать с любым каноническим ID (`06-model-registry.md`, «Уникальность
    aliases»).
    """
    ordered = sorted(Path(path) for path in files)
    records = [load_model_file(path) for path in ordered]

    by_id: dict[str, Path] = {}
    alias_owner: dict[str, Path] = {}
    for path, record in zip(ordered, records, strict=True):
        if record.model_id in by_id:
            raise DuplicateModelIdError(record.model_id, path=path)
        by_id[record.model_id] = path

    for path, record in zip(ordered, records, strict=True):
        for alias in record.aliases:
            if alias in by_id:
                raise DuplicateAliasError(
                    alias, conflict=f"alias совпадает с каноническим ID {alias!r}", path=path
                )
            if alias in alias_owner:
                raise DuplicateAliasError(
                    alias,
                    conflict=f"alias уже занят записью {alias_owner[alias].name}",
                    path=path,
                )
            alias_owner[alias] = path

    return tuple(records)


def registry_files(root: Path) -> tuple[Path, ...]:
    """Список YAML-файлов Registry под каталогом, в стабильном порядке."""
    if not root.is_dir():
        return ()
    return tuple(sorted(root.rglob("*.yaml")))


def load_registry_root(root: Path) -> tuple[ModelRecord, ...]:
    """Загрузить все записи Registry из каталога (пустой каталог — пустой набор)."""
    return load_registry(registry_files(root))


__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "load_model_file",
    "load_registry",
    "load_registry_root",
    "parse_model_record",
    "registry_files",
]
