"""SQLite leaf guards reject redirects before connect/migrations touch a target."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import StorageError
from aimedia.storage.migrations import MIGRATIONS, apply_migrations


def schema(path: Path):
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()


@pytest.mark.parametrize("version", [1, 2])
def test_redirected_database_leaf_cannot_migrate_external_disposable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
):
    target = tmp_path / "outside.sqlite3"
    original = DatabaseManager(target)
    original.connect()
    apply_migrations(original.database, MIGRATIONS[:version])
    original.close()
    before_bytes, before_schema = target.read_bytes(), schema(target)
    root = tmp_path / "data"
    root.mkdir()
    leaf = root / "database.sqlite3"
    leaf.symlink_to(target)
    manager = DatabaseManager(leaf)

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("SQLite must not open a redirected leaf")

    monkeypatch.setattr(manager._database, "connect", forbidden_connect)
    with pytest.raises(StorageError):
        manager.open()
    assert not manager.is_open
    assert target.read_bytes() == before_bytes
    assert schema(target) == before_schema
    assert leaf.is_symlink()  # No destructive cleanup, including the redirect itself.


def test_database_leaf_is_checked_again_before_explicit_migration(tmp_path, monkeypatch):
    manager = DatabaseManager(tmp_path / "database.sqlite3")
    manager.connect()
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda p: p == manager.path or original(p))
    try:
        with pytest.raises(StorageError):
            manager.migrate()
        assert manager.database.get_tables() == []
    finally:
        manager.close()
