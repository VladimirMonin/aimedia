"""Встроенный (production) каталог Registry как package resource (E03).

Loader не должен зависеть от текущей рабочей директории
(`06-model-registry.md`, «Model Registry как package resource», «Runtime path
resolution»). Поэтому путь к данным берётся через package resources, а не через
относительный `Path("./registry/models")`.

На E03 встроенный каталог намеренно пуст: в локальных документах нет
authoritative remote ID и лимитов, а синтетические записи вне тестов запрещены
(`docs/plans/release-scope.md`; решение E00 D16). Пустой каталог — это явное
ограничение **not verified**, а не permissive fallback и не подтверждённая
поддержка моделей: см. `aimedia/registry/data/README.md`.
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
    """Загрузить встроенный каталог (сейчас — пустой набор).

    Отсутствие записей не маскируется синтетическими ID: пустой кортеж — честный
    результат, а не «модель есть по умолчанию».
    """
    return load_registry_root(builtin_registry_dir())


__all__ = [
    "builtin_registry_dir",
    "load_builtin_registry",
]
