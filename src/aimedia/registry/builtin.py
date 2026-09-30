"""Встроенный (production) каталог Registry как package resource (E03).

Loader не должен зависеть от текущей рабочей директории
(`06-model-registry.md`, «Model Registry как package resource», «Runtime path
resolution»). Поэтому путь к данным берётся через package resources, а не через
относительный `Path("./registry/models")`.

Каталог содержит только официально документированные experimental bindings,
не live_verified модели: см. `aimedia/registry/data/README.md`. Синтетические IDs
в production запрещены (D16), текущие tiers не являются гарантией будущей цены.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from aimedia.registry.loader import load_registry_root
from aimedia.registry.models import ModelRecord

_BUILTIN_DATA_PACKAGE = "aimedia.registry.data"


def builtin_registry_dir() -> Path:
    """Каталог встроенных YAML-записей Registry внутри установленного пакета."""
    return Path(str(files(_BUILTIN_DATA_PACKAGE)))


def load_builtin_registry() -> tuple[ModelRecord, ...]:
    """Загрузить строгий documented каталог; запись не доказывает live-поддержку."""
    return load_registry_root(builtin_registry_dir())


__all__ = [
    "builtin_registry_dir",
    "load_builtin_registry",
]
