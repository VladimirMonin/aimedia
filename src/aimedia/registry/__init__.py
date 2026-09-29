"""Model Registry: строгая загрузка декларативных YAML-записей моделей.

Пакет отделён от домена (`aimedia.domain` IO-free) и от provider adapter: загрузка
является полностью offline-операцией над локальными данными
(`docs/plans/06-model-registry.md`).
"""

from __future__ import annotations

from aimedia.registry.builtin import builtin_registry_dir, load_builtin_registry
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
from aimedia.registry.events import (
    MODEL_RESOLVED_EVENT,
    MODEL_VALIDATION_FAILED_EVENT,
    REGISTRY_LOADED_EVENT,
    log_model_resolved,
    log_model_validation_failed,
    log_registry_loaded,
    registry_digest,
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
from aimedia.registry.views import (
    CapabilityView,
    InputLimitView,
    ModelSummaryView,
    ModelView,
    ParameterView,
    ProvenanceStatus,
    ProvenanceView,
    build_model_list,
    build_model_view,
    render_model_help,
)

__all__ = [
    "MODEL_RESOLVED_EVENT",
    "MODEL_VALIDATION_FAILED_EVENT",
    "REGISTRY_LOADED_EVENT",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CapabilityNode",
    "CapabilityView",
    "DocsRef",
    "DuplicateAliasError",
    "DuplicateModelIdError",
    "DuplicateYamlKeyError",
    "EffectiveModelDefinition",
    "Family",
    "InputLimit",
    "InputLimitView",
    "InvalidModelRecordError",
    "InvalidProviderOverrideError",
    "InvalidRegistryFileError",
    "ModelNotAvailableOnProviderError",
    "ModelRecord",
    "ModelResolver",
    "ModelStatus",
    "ModelSummaryView",
    "ModelView",
    "ParameterOverride",
    "ParameterSpec",
    "ParameterType",
    "ParameterView",
    "ProviderBinding",
    "ProvenanceStatus",
    "ProvenanceView",
    "RegistryError",
    "UnknownModelError",
    "UnsupportedSchemaVersionError",
    "UnsafeYamlTagError",
    "ValidatedModelRequest",
    "Verification",
    "build_model_list",
    "build_model_view",
    "builtin_registry_dir",
    "load_builtin_registry",
    "load_model_file",
    "load_registry",
    "load_registry_root",
    "log_model_resolved",
    "log_model_validation_failed",
    "log_registry_loaded",
    "parse_model_record",
    "registry_digest",
    "registry_files",
    "render_model_help",
    "resolve_model",
    "validate_model_request",
]
