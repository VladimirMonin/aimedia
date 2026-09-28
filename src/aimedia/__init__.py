"""aimedia — локальный CLI генерации изображений через Polza."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION_NAME = "aimedia"


def get_version() -> str:
    """Вернуть версию установленного пакета.

    Единственный источник версии — metadata distribution, которую задаёт
    `pyproject.toml`. Значение версии не дублируется в исходниках.
    """
    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:  # pragma: no cover - пакет не установлен
        return "0.0.0+unknown"


__version__ = get_version()
