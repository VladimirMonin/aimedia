"""Public storage failures use one safe JSON envelope and exit 8, not INTERNAL."""

from __future__ import annotations

import json

import pytest
from peewee import OperationalError, ProgrammingError, SqliteDatabase
from typer.testing import CliRunner

from aimedia.cli.app import app


@pytest.fixture
def isolated_argv(tmp_path):
    return ["--config", str(tmp_path / "missing.toml"), "--data-dir", str(tmp_path / "data")]


def storage_failure(argv):
    result = CliRunner().invoke(app, [*argv, "--json"])
    assert result.exit_code == 8, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert not payload["ok"] and payload["error"]["code"] == "DATABASE_ERROR"
    assert "backend_canary" not in result.stdout + result.stderr


def test_public_history_directory_database_leaf_is_storage_error(tmp_path, isolated_argv):
    (tmp_path / "data" / "database.sqlite3").mkdir(parents=True)
    storage_failure([*isolated_argv, "jobs", "recent"])


def test_public_history_expected_connect_failure_is_storage_error(monkeypatch, isolated_argv):
    def fail(*args, **kwargs):
        raise OperationalError("backend_canary: permission denied")

    monkeypatch.setattr(SqliteDatabase, "connect", fail)
    storage_failure([*isolated_argv, "jobs", "recent"])


def test_public_history_expected_search_failure_is_storage_error(monkeypatch, isolated_argv):
    execute = SqliteDatabase.execute_sql

    def fail_search(self, sql, *args, **kwargs):
        if "jobs_fts MATCH" in sql:
            raise OperationalError("backend_canary: disk read failure")
        return execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(SqliteDatabase, "execute_sql", fail_search)
    storage_failure([*isolated_argv, "jobs", "search", "robot"])


@pytest.mark.parametrize("error_type", [TypeError, ProgrammingError])
def test_public_history_unexpected_backend_bug_is_not_disguised_as_storage(
    monkeypatch, isolated_argv, error_type
):
    def fail(*args, **kwargs):
        raise error_type("backend_canary: programming bug")

    monkeypatch.setattr(SqliteDatabase, "connect", fail)
    result = CliRunner().invoke(app, [*isolated_argv, "jobs", "recent", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "INTERNAL_ERROR"
    assert "backend_canary" not in result.stdout + result.stderr


def test_public_history_corrupt_database_is_storage_error_without_cleanup(tmp_path, isolated_argv):
    root = tmp_path / "data"
    root.mkdir()
    leaf = root / "database.sqlite3"
    content = b"not a SQLite database; disposable bytes"
    leaf.write_bytes(content)
    storage_failure([*isolated_argv, "jobs", "recent"])
    assert leaf.read_bytes() == content
