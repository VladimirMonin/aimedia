"""Отказ миграции: успех не записан, половина схемы не остаётся, FK соблюдены.

E04, C06a: каждая миграция выполняется в собственной транзакции. Поэтому падение в
любой её точке откатывает и частичные DDL, и запись версии. Тесты проверяют именно
это, а не факт исключения: после сбоя в `schema_migrations` нет ложной записи, в
`sqlite_master` нет половинчатой таблицы, а ранее применённые данные целы.

Все базы — во временных каталогах.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from peewee import Database, IntegrityError

from aimedia.storage import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    DatabaseManager,
    Migration,
    MigrationFailedError,
    applied_migrations,
    apply_migrations,
    current_schema_version,
    open_database,
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "database.sqlite3"


def _failing_chain() -> tuple[Migration, ...]:
    """v1 успешна, v2 создаёт таблицу и падает — частичный DDL обязан исчезнуть."""

    def good_v1(database: Database) -> None:
        database.execute_sql(
            'CREATE TABLE "alpha" ("id" INTEGER NOT NULL PRIMARY KEY, "name" TEXT NOT NULL)'
        )

    def broken_v2(database: Database) -> None:
        database.execute_sql('CREATE TABLE "beta" ("id" INTEGER NOT NULL PRIMARY KEY)')
        database.execute_sql('INSERT INTO "alpha" ("id", "name") VALUES (2, ?)', ("вторая",))
        raise RuntimeError("синтетический сбой миграции v2")

    return (
        Migration(version=1, name="001_alpha", apply=good_v1),
        Migration(version=2, name="002_beta", apply=broken_v2),
    )


def _fk_violating_chain() -> tuple[Migration, ...]:
    """v2 нарушает внешний ключ: при выключенном FK это прошло бы незаметно."""

    def good_v1(database: Database) -> None:
        database.execute_sql(
            'CREATE TABLE "parents" ("id" INTEGER NOT NULL PRIMARY KEY, "name" TEXT NOT NULL)'
        )
        database.execute_sql(
            'CREATE TABLE "children" ('
            '"id" INTEGER NOT NULL PRIMARY KEY, '
            '"parent_id" INTEGER NOT NULL, '
            'FOREIGN KEY ("parent_id") REFERENCES "parents" ("id"))'
        )

    def broken_v2(database: Database) -> None:
        database.execute_sql('INSERT INTO "children" ("id", "parent_id") VALUES (1, 404)')

    return (
        Migration(version=1, name="001_parents", apply=good_v1),
        Migration(version=2, name="002_children", apply=broken_v2),
    )


def test_failed_migration_is_rolled_back_and_not_recorded(tmp_path: Path) -> None:
    """Отказ v2: её таблица отсутствует, а версия 2 не записана как успешная."""
    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        chain = _failing_chain()
        outcome = apply_migrations(manager.database, chain[:1])
        assert outcome.applied == (1,)
        manager.database.execute_sql(
            'INSERT INTO "alpha" ("id", "name") VALUES (1, ?)', ("первая",)
        )

        with pytest.raises(MigrationFailedError) as excinfo:
            apply_migrations(manager.database, chain)

        assert excinfo.value.version == 2
        assert excinfo.value.name == "002_beta"
        # Исходная причина сохранена: откат не подменяет диагностику.
        assert isinstance(excinfo.value.__cause__, RuntimeError)

        tables = set(manager.database.get_tables())
        assert "alpha" in tables
        assert "beta" not in tables
        assert "schema_migrations" in tables

        assert current_schema_version(manager.database) == 1
        assert applied_migrations(manager.database) == {1: "001_alpha"}

        # Ранее применённые данные целы, а частичная вставка v2 откатилась.
        assert manager.database.execute_sql('SELECT "id", "name" FROM "alpha"').fetchall() == [
            (1, "первая")
        ]
    finally:
        manager.close()


def test_database_not_left_half_migrated_on_real_schema(tmp_path: Path) -> None:
    """Даже на продуктовой схеме отказ не оставляет половину таблиц."""
    path = _database_path(tmp_path)

    def broken_v1(database: Database) -> None:
        # Первая таблица создаётся, затем миграция падает: без транзакции она бы
        # осталась в БД как «наполовину обновлённая» схема.
        database.execute_sql('CREATE TABLE "orphan" ("id" INTEGER NOT NULL PRIMARY KEY)')
        database.execute_sql("THIS IS NOT SQL")

    chain = (Migration(version=1, name="001_broken_initial", apply=broken_v1),)

    manager = DatabaseManager(path)
    manager.connect()
    try:
        with pytest.raises(MigrationFailedError):
            apply_migrations(manager.database, chain)

        tables = set(manager.database.get_tables())
        assert "orphan" not in tables
        assert current_schema_version(manager.database) == 0
        assert applied_migrations(manager.database) == {}
    finally:
        manager.close()

    # База осталась пригодной: корректная миграция с тем же номером применяется.
    reopened = open_database(path)
    try:
        assert current_schema_version(reopened.database) == LATEST_SCHEMA_VERSION
        assert {"jobs", "prompt_sources", "inputs", "artifacts"} <= set(
            reopened.database.get_tables()
        )
    finally:
        reopened.close()


def test_failed_migration_does_not_consume_version_number(tmp_path: Path) -> None:
    """Повторная попытка после сбоя не пропускает версию и не видит её применённой."""
    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        chain = _failing_chain()
        with pytest.raises(MigrationFailedError):
            apply_migrations(manager.database, chain)

        # Успешная v1 записана, упавшая v2 — нет.
        assert applied_migrations(manager.database) == {1: "001_alpha"}

        # Повторный прогон той же цепочки снова падает на v2, а не считает её "уже
        # применённой": версия берётся из БД, а не из неудачной попытки.
        with pytest.raises(MigrationFailedError) as excinfo:
            apply_migrations(manager.database, chain)
        assert excinfo.value.version == 2
        assert applied_migrations(manager.database) == {1: "001_alpha"}
    finally:
        manager.close()


def test_foreign_key_violation_inside_migration_fails_and_rolls_back(tmp_path: Path) -> None:
    """`foreign_keys=ON` действует внутри миграции: нарушение FK — отказ и откат."""
    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        assert manager.database.execute_sql("PRAGMA foreign_keys").fetchone() == (1,)

        chain = _fk_violating_chain()
        apply_migrations(manager.database, chain[:1])

        with pytest.raises(MigrationFailedError) as excinfo:
            apply_migrations(manager.database, chain)

        assert isinstance(excinfo.value.__cause__, IntegrityError)
        assert manager.database.execute_sql('SELECT COUNT(*) FROM "children"').fetchone() == (0,)
        assert applied_migrations(manager.database) == {1: "001_parents"}
    finally:
        manager.close()


def _product_chain_with(broken_migration: Callable[[Database], None]) -> tuple[Migration, ...]:
    """Продуктовая цепочка (реальная схема) плюс синтетическая падающая версия."""
    return (
        *MIGRATIONS,
        Migration(
            version=LATEST_SCHEMA_VERSION + 1,
            name="999_synthetic_broken",
            apply=broken_migration,
        ),
    )


def test_failed_v2_leaves_production_v1_rows_and_schema_untouched(tmp_path: Path) -> None:
    """Отказ шага v2 на реальной v1: строки целы, таблицы v2 нет, повтор проходит."""
    path = _database_path(tmp_path)
    manager = DatabaseManager(path)
    manager.connect()
    try:
        assert apply_migrations(manager.database, MIGRATIONS[:1]).applied == (1,)
        manager.database.execute_sql(
            'INSERT INTO "jobs" ("id", "kind", "status", "provider_id", "model_id", "created_at") '
            "VALUES (1, 'image_generate', 'completed', 'polza', 'synthetic-model', ?)",
            ("2026-01-01T00:00:00Z",),
        )
        manager.database.execute_sql(
            'INSERT INTO "inputs" '
            '("id", "job_id", "kind", "position", "source_path", "sha256") '
            "VALUES (1, 1, 'image', 0, 'refs/robot.png', ?)",
            ("a" * 64,),
        )

        def broken_v2(database: Database) -> None:
            database.execute_sql(
                'CREATE TABLE "managed_input_copies" ('
                '"input_id" INTEGER NOT NULL PRIMARY KEY, "local_path" TEXT NOT NULL)'
            )
            raise RuntimeError("сбой после создания таблицы v2")

        chain = (
            MIGRATIONS[0],
            Migration(version=2, name="002_synthetic_failed", apply=broken_v2),
        )
        with pytest.raises(MigrationFailedError) as excinfo:
            apply_migrations(manager.database, chain)

        assert excinfo.value.version == 2
        assert "managed_input_copies" not in set(manager.database.get_tables())
        assert current_schema_version(manager.database) == 1
        assert applied_migrations(manager.database) == {1: "001_initial"}
        assert manager.database.execute_sql(
            'SELECT "source_path", "sha256" FROM "inputs" WHERE "id" = 1'
        ).fetchone() == ("refs/robot.png", "a" * 64)

        # Номер версии не «сгорел»: штатная v2 применяется со следующей попытки, а
        # уже записанные строки v1 остаются без managed-копий.
        outcome = apply_migrations(manager.database)
        assert outcome.applied == (2,)
        assert "managed_input_copies" in set(manager.database.get_tables())
        assert manager.database.execute_sql('SELECT COUNT(*) FROM "inputs"').fetchone() == (1,)
        assert manager.database.execute_sql(
            'SELECT COUNT(*) FROM "managed_input_copies"'
        ).fetchone() == (0,)
    finally:
        manager.close()


def test_real_schema_keeps_foreign_keys_enforced_after_failed_migration(tmp_path: Path) -> None:
    """Провал миграции не отключает FK: после него схема всё ещё отклоняет сироту."""
    manager = open_database(_database_path(tmp_path))

    def broken_v2(database: Database) -> None:
        database.execute_sql('CREATE TABLE "half" ("id" INTEGER NOT NULL PRIMARY KEY)')
        raise RuntimeError("сбой после частичного DDL")

    try:
        with pytest.raises(MigrationFailedError):
            apply_migrations(manager.database, _product_chain_with(broken_v2))

        assert "half" not in set(manager.database.get_tables())
        with pytest.raises(IntegrityError):
            manager.database.execute_sql(
                'INSERT INTO "artifacts" '
                '("job_id", "kind", "role", "position", "created_at") VALUES (?, ?, ?, ?, ?)',
                (123, "image", "final", 0, "2026-01-01T00:00:00Z"),
            )
    finally:
        manager.close()
