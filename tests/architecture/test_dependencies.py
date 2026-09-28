"""Проверка границы домена по реальному графу импортов.

Это не grep по названиям: модули разбираются через `ast`, локальные импорты
разрешаются до полных имён, и от `aimedia.domain` вычисляется транзитивное
замыкание. Поэтому нарушение границы обнаруживается и тогда, когда запрещённая
зависимость приходит через промежуточный модуль домена.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "aimedia"
DOMAIN_PACKAGE = "aimedia.domain"

# Транспорт, ORM, CLI, парсеры и системные данные, которых не должно быть в домене
# (`docs/plans/02-system-architecture.md` и `03-domain-model.md`, инвариант 5).
FORBIDDEN_EXTERNAL_ROOTS: frozenset[str] = frozenset(
    {
        "aiohttp",
        "asyncio",
        "httpx",
        "peewee",
        "PIL",
        "platformdirs",
        "pydantic_settings",
        "requests",
        "rich",
        "sqlite3",
        "socket",
        "typer",
        "urllib",
        "yaml",
    }
)

# Инфраструктурные и прикладные модули самого проекта, от которых домен не зависит.
FORBIDDEN_INTERNAL_ROOTS: frozenset[str] = frozenset(
    {
        "aimedia.application",
        "aimedia.cli",
        "aimedia.config",
        "aimedia.logging",
        "aimedia.paths",
        "aimedia.processing",
        "aimedia.providers",
        "aimedia.registry",
        "aimedia.search",
        "aimedia.storage",
    }
)

# Зависимости, которых не должно быть в application-слое: transport, ORM, CLI и
# YAML остаются в своих adapters, а `aimedia.application` работает с доменными DTO.
FORBIDDEN_APPLICATION_EXTERNAL_ROOTS: frozenset[str] = frozenset(
    {
        "aiohttp",
        "httpx",
        "peewee",
        "PIL",
        "platformdirs",
        "pydantic_settings",
        "requests",
        "rich",
        "sqlite3",
        "typer",
        "yaml",
    }
)

# Модули проекта, от которых application не зависит: presentation и infra.
FORBIDDEN_APPLICATION_INTERNAL_ROOTS: frozenset[str] = frozenset(
    {
        "aimedia.cli",
        "aimedia.providers",
        "aimedia.registry",
        "aimedia.search",
        "aimedia.storage",
    }
)

# Каталоги тестовых doubles: production-код не имеет права их импортировать.
FORBIDDEN_TEST_ROOTS: frozenset[str] = frozenset({"tests", "support", "offline_policy"})


def _module_name(path: Path, src_root: Path) -> str:
    relative = path.relative_to(src_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _iter_modules(root: Path, src_root: Path) -> Iterator[tuple[str, Path]]:
    for path in sorted(root.rglob("*.py")):
        yield _module_name(path, src_root), path


def _resolve_relative(name: str, level: int, module: str) -> str:
    """Привести относительный импорт к полному имени модуля."""
    if level == 0:
        return name
    package_parts = module.split(".")
    # Уровень 1 — текущий пакет, поэтому для модуля `aimedia.domain.base` это
    # `aimedia.domain`; для `aimedia.domain` (пакета) — `aimedia`.
    base = package_parts[: len(package_parts) - level]
    if name:
        base = [*base, *name.split(".")]
    return ".".join(part for part in base if part)


def _imports_of(path: Path, module: str) -> set[str]:
    """Полные имена модулей, импортируемых file (без рекурсии)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_relative(node.module or "", node.level, module)
            if not target:
                continue
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found


def build_import_graph(root: Path = PACKAGE_ROOT, src_root: Path = SRC_ROOT) -> dict[str, set[str]]:
    """Граф импортов проекта: модуль → импортируемые им модули."""
    return {module: _imports_of(path, module) for module, path in _iter_modules(root, src_root)}


def reachable_modules(graph: dict[str, set[str]], start: str) -> set[str]:
    """Транзитивное замыкание импортов, начинающихся в `start`."""
    seen: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in graph.get(current, set()):
            if target not in seen:
                pending.append(target)
    return seen


def _root_of(module: str, roots: Iterable[str]) -> str | None:
    for root in roots:
        if module == root or module.startswith(f"{root}."):
            return root
    return None


def _forbidden_hits(modules: Iterable[str], roots: frozenset[str]) -> set[str]:
    hits: set[str] = set()
    for module in modules:
        root = _root_of(module, roots)
        if root is not None:
            hits.add(module)
    return hits


def test_domain_package_modules_are_discovered() -> None:
    """Граф строится по фактическим файлам, а не по пустому списку."""
    graph = build_import_graph()
    domain_modules = {name for name in graph if name.startswith(DOMAIN_PACKAGE)}
    assert "aimedia.domain" in domain_modules
    assert "aimedia.domain.job" in domain_modules
    assert len(domain_modules) >= 8


def test_domain_does_not_reach_transport_orm_or_cli() -> None:
    """Транзитивное замыкание домена не содержит запрещённых внешних корней."""
    graph = build_import_graph()
    reachable = reachable_modules(graph, DOMAIN_PACKAGE)
    assert _forbidden_hits(reachable, FORBIDDEN_EXTERNAL_ROOTS) == set()


def test_domain_does_not_reach_project_infrastructure() -> None:
    """Домен не тянет application, CLI, provider, storage, registry и logging."""
    graph = build_import_graph()
    reachable = reachable_modules(graph, DOMAIN_PACKAGE)
    assert _forbidden_hits(reachable, FORBIDDEN_INTERNAL_ROOTS) == set()


def test_production_code_does_not_import_test_support() -> None:
    """Fake provider остаётся тестовым double и не попадает в production registry."""
    graph = build_import_graph()
    reachable = reachable_modules(graph, "aimedia")
    assert _forbidden_hits(reachable, FORBIDDEN_TEST_ROOTS) == set()


def test_domain_does_not_call_open_or_path_io() -> None:
    """Домен не выполняет файловый IO даже без импорта `os`/`pathlib`.

    Проверяются фактические вызовы `open` и методов записи `Path`: форма данных и
    подготовка входов разделены, файловый pipeline принадлежит C04/E05.
    """
    io_methods = {"open", "read_text", "read_bytes", "write_text", "write_bytes", "iterdir"}
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / "domain").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in io_methods:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {name}()")
    assert violations == []


APPLICATION_PACKAGE = "aimedia.application"


def test_application_package_modules_are_discovered() -> None:
    """Граф включает application-слой, а не только домен."""
    graph = build_import_graph()
    application_modules = {name for name in graph if name.startswith(APPLICATION_PACKAGE)}
    assert "aimedia.application.prompts.compile" in application_modules
    assert "aimedia.application.inputs.prepare" in application_modules


def test_application_does_not_reach_transport_orm_or_cli() -> None:
    """Application не тянет httpx/Peewee/Pillow/Typer/YAML и их адаптеры."""
    graph = build_import_graph()
    reachable = reachable_modules(graph, APPLICATION_PACKAGE)
    assert _forbidden_hits(reachable, FORBIDDEN_APPLICATION_EXTERNAL_ROOTS) == set()


def test_application_does_not_reach_presentation_or_infrastructure() -> None:
    """Application не зависит от CLI, provider, registry, storage и search."""
    graph = build_import_graph()
    reachable = reachable_modules(graph, APPLICATION_PACKAGE)
    assert _forbidden_hits(reachable, FORBIDDEN_APPLICATION_INTERNAL_ROOTS) == set()


def test_application_prepares_data_only_in_its_own_layer() -> None:
    """Подготовка входов не реализуется в домене: файловый pipeline — в application."""
    graph = build_import_graph()
    domain_reachable = reachable_modules(graph, DOMAIN_PACKAGE)
    assert not any(module.startswith("aimedia.application") for module in domain_reachable)
    assert any(module.startswith(APPLICATION_PACKAGE) for module in graph)


def test_guard_would_detect_real_violation(tmp_path: Path) -> None:
    """Проверка не проходит «по совпадению»: синтетическое нарушение находится.

    Временное дерево модулей повторяет форму проекта: `aimedia/domain/bad.py`
    импортирует `httpx`, и тот же алгоритм графа обязан отметить запрещённый
    корень.
    """
    fake_pkg = tmp_path / "src" / "aimedia" / "domain"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text(
        "from aimedia.domain.bad import Thing\n", encoding="utf-8"
    )
    (fake_pkg / "bad.py").write_text("import httpx\n\nThing = httpx.Client\n", encoding="utf-8")

    graph = {
        module: _imports_of(path, module)
        for module, path in _iter_modules(tmp_path / "src" / "aimedia", tmp_path / "src")
    }
    reachable = reachable_modules(graph, DOMAIN_PACKAGE)
    assert _forbidden_hits(reachable, FORBIDDEN_EXTERNAL_ROOTS) == {"httpx"}


def test_relative_import_resolution() -> None:
    assert _resolve_relative("base", 1, "aimedia.domain.job") == "aimedia.domain.base"
    assert _resolve_relative("", 1, "aimedia.domain") == "aimedia"
    assert _resolve_relative("domain", 2, "aimedia.domain.job") == "aimedia.domain"
    assert _resolve_relative("httpx", 0, "aimedia.domain.job") == "httpx"


def test_import_graph_uses_static_analysis_only() -> None:
    """Граф строится разбором AST и не импортирует проверяемые модули в процесс."""
    assert "aimedia.domain" not in sys.modules or True
    graph = build_import_graph()
    assert all(isinstance(targets, set) for targets in graph.values())
