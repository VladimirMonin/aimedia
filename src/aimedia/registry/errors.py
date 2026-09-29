"""Типизированные ошибки загрузки Model Registry.

Registry — отдельный инфраструктурный слой (`aimedia.registry`), поэтому его
ошибки не входят в доменный `DomainErrorCode`: они возникают при чтении
декларативных данных до построения request и не являются отказом provider.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class RegistryError(Exception):
    """Базовая ошибка Registry с машинно различимым `code`."""

    code = "REGISTRY_ERROR"

    def __init__(
        self,
        message: str,
        *,
        path: object | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.details: dict[str, Any] = dict(details or {})


class InvalidRegistryFileError(RegistryError):
    """Файл не является корректным одиночным YAML-документом Registry."""

    code = "INVALID_REGISTRY_FILE"


class DuplicateYamlKeyError(RegistryError):
    """Один и тот же ключ встречается в одном mapping дважды."""

    code = "DUPLICATE_YAML_KEY"

    def __init__(self, key: str, *, path: object | None = None, line: int | None = None) -> None:
        super().__init__(
            f"дублирующийся YAML-ключ: {key!r}",
            path=path,
            details={"key": key, "line": line},
        )
        self.key = key


class UnsafeYamlTagError(RegistryError):
    """Документ содержит тег за пределами разрешённого data-only набора."""

    code = "UNSAFE_YAML_TAG"

    def __init__(self, tag: str, *, path: object | None = None, line: int | None = None) -> None:
        super().__init__(
            f"запрещённый YAML-тег: {tag!r}; Registry допускает только data-only теги",
            path=path,
            details={"tag": tag, "line": line},
        )
        self.tag = tag


class UnsupportedSchemaVersionError(RegistryError):
    """`schema_version` файла не поддерживается загрузчиком."""

    code = "UNSUPPORTED_SCHEMA_VERSION"

    def __init__(
        self, version: object, *, supported: Iterable[int], path: object | None = None
    ) -> None:
        supported_tuple = tuple(sorted(supported))
        super().__init__(
            f"неизвестная schema_version: {version!r}; поддерживаются {supported_tuple}",
            path=path,
            details={"version": version, "supported": list(supported_tuple)},
        )
        self.version = version


class InvalidModelRecordError(RegistryError):
    """Запись модели не соответствует схеме Registry."""

    code = "INVALID_MODEL_RECORD"

    def __init__(
        self, *, path: object | None = None, errors: Iterable[dict[str, Any]] | None = None
    ) -> None:
        details = list(errors or [])
        super().__init__(
            "запись модели нарушает схему Registry",
            path=path,
            details={"errors": details},
        )
        self.errors = details


class DuplicateModelIdError(RegistryError):
    """Канонический ID модели встречается больше одного раза."""

    code = "DUPLICATE_MODEL_ID"

    def __init__(self, model_id: str, *, path: object | None = None) -> None:
        super().__init__(
            f"дублирующийся канонический ID модели: {model_id!r}",
            path=path,
            details={"model_id": model_id},
        )
        self.model_id = model_id


class DuplicateAliasError(RegistryError):
    """Alias повторно используется или конфликтует с каноническим ID."""

    code = "DUPLICATE_ALIAS"

    def __init__(self, alias: str, *, conflict: str, path: object | None = None) -> None:
        super().__init__(
            f"конфликт alias {alias!r}: {conflict}",
            path=path,
            details={"alias": alias, "conflict": conflict},
        )
        self.alias = alias


class UnknownModelError(RegistryError):
    """Запрошенный канонический ID или alias отсутствует в Registry.

    Это отдельная ошибка слоя Registry (как и остальные здесь), а не доменный
    `aimedia.domain.errors.UnknownModelError`; строковый `code` совпадает с
    пользовательским `UNKNOWN_MODEL` (`06-model-registry.md`, «Unknown model»).
    """

    code = "UNKNOWN_MODEL"

    def __init__(self, model_id_or_alias: str, *, path: object | None = None) -> None:
        super().__init__(
            f"модель {model_id_or_alias!r} отсутствует в Registry",
            path=path,
            details={"model_id_or_alias": model_id_or_alias},
        )
        self.model_id_or_alias = model_id_or_alias


class ModelNotAvailableOnProviderError(RegistryError):
    """У модели нет binding к выбранному provider.

    Модель существует, но у этого provider нет `remote_model_id`: разрешать её
    молча нельзя (`06-model-registry.md`, «Unknown provider binding»).
    """

    code = "MODEL_NOT_AVAILABLE_ON_PROVIDER"

    def __init__(
        self,
        model_id: str,
        provider_id: str,
        *,
        available: Iterable[str] = (),
        path: object | None = None,
    ) -> None:
        available_tuple = tuple(available)
        super().__init__(
            f"модель {model_id!r} не настроена для provider {provider_id!r}; "
            f"доступные providers: {available_tuple}",
            path=path,
            details={
                "model_id": model_id,
                "provider_id": provider_id,
                "available": list(available_tuple),
            },
        )
        self.model_id = model_id
        self.provider_id = provider_id
        self.available = available_tuple


class InvalidProviderOverrideError(RegistryError):
    """Provider override нарушает правило сужения базовой модели.

    Binding может только ограничивать базовую capability: расширение набора
    значений или границ, несовместимый default и override несуществующего
    параметра — ошибка схемы, а не «победа override»
    (`06-model-registry.md`, «Принцип override»; `docs/plans/README.md`, E03).
    """

    code = "INVALID_PROVIDER_OVERRIDE"

    def __init__(self, parameter: str, reason: str, *, path: object | None = None) -> None:
        super().__init__(
            f"недопустимое сужение параметра {parameter!r}: {reason}",
            path=path,
            details={"parameter": parameter, "reason": reason},
        )
        self.parameter = parameter
        self.reason = reason
