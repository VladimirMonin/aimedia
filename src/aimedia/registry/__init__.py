"""Model Registry: строгая загрузка декларативных YAML-записей моделей.

Пакет отделён от домена (`aimedia.domain` IO-free) и от provider adapter: загрузка
является полностью offline-операцией над локальными данными
(`docs/plans/06-model-registry.md`).
"""

from __future__ import annotations

from aimedia.registry.errors import (
    DuplicateAliasError,
    DuplicateModelIdError,
    DuplicateYamlKeyError,
    InvalidModelRecordError,
    InvalidProviderOverrideError,
    InvalidRegistryFileError,
    ModelNotAvailableOnProviderError,
    RegistryError,
    UnknownModelError,
    UnsafeYamlTagError,
    UnsupportedSchemaVersionError,
)
from aimedia.registry.loader import (
    SUPPORTED_SCHEMA_VERSIONS,
    load_model_file,
    load_registry,
    load_registry_root,
    parse_model_record,
    registry_files,
)
from aimedia.registry.models import (
    CapabilityNode,
    DocsRef,
    EffectiveModelDefinition,
    Family,
    InputLimit,
    ModelRecord,
    ModelStatus,
    ParameterOverride,
    ParameterSpec,
    ParameterType,
    ProviderBinding,
    Verification,
)
from aimedia.registry.resolver import ModelResolver, resolve_model
from aimedia.registry.validator import ValidatedModelRequest, validate_model_request

__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "CapabilityNode",
    "DocsRef",
    "DuplicateAliasError",
    "DuplicateModelIdError",
    "DuplicateYamlKeyError",
    "EffectiveModelDefinition",
    "Family",
    "InputLimit",
    "InvalidModelRecordError",
    "InvalidProviderOverrideError",
    "InvalidRegistryFileError",
    "ModelNotAvailableOnProviderError",
    "ModelRecord",
    "ModelResolver",
    "ModelStatus",
    "ParameterOverride",
    "ParameterSpec",
    "ParameterType",
    "ProviderBinding",
    "RegistryError",
    "UnknownModelError",
    "UnsupportedSchemaVersionError",
    "UnsafeYamlTagError",
    "ValidatedModelRequest",
    "Verification",
    "load_model_file",
    "load_registry",
    "load_registry_root",
    "parse_model_record",
    "registry_files",
    "resolve_model",
    "validate_model_request",
]
