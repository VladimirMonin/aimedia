"""Жизненный цикл схемы storage: пустая и предыдущая схема, no-op, FK, ownership.

Проверяется единственный migration runner (E04, C06a): пустая БД доводится до
последней версии, синтетическая предыдущая версия обновляется без потери данных,
повторный прогон ничего не меняет, а база «из будущего» не понижается. Отдельно
проверяются pragmas и владение соединением: `foreign_keys=ON`, WAL, `busy_timeout`
и отказ чужому потоку.

Все базы живут в `tmp_path`; пользовательский data-root не создаётся.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from peewee import Database, IntegrityError, OperationalError

from aimedia.storage import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    DatabaseClosedError,
    DatabaseManager,
    DatabaseOwnershipError,
    Migration,
    MigrationDefinitionError,
    SchemaTooNewError,
    StorageError,
    applied_migrations,
    apply_migrations,
    current_schema_version,
    default_pragmas,
    open_database,
    parse_version,
    validate_migrations,
    verify_engine_versions,
)

DATABASE_FILENAME = "database.sqlite3"
EXPECTED_TABLES = frozenset({"jobs", "prompt_sources", "inputs", "artifacts", "schema_migrations"})


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILENAME


def _synthetic_previous_schema() -> tuple[Migration, ...]:
    """Синтетическая цепочка v1 → v2 для проверки обновления предыдущей схемы.

    v1 создаёт только `widgets`, v2 добавляет колонку и дочернюю таблицу. Это не
    продуктовая схема, а проверяемое утверждение плана: «previous schema
    обновляется, существующие данные остаются».
    """

    def widget_v1(database: Database) -> None:
        database.execute_sql(
            'CREATE TABLE "widgets" ("id" INTEGER NOT NULL PRIMARY KEY, "name" TEXT NOT NULL)'
        )

    def widget_v2(database: Database) -> None:
        database.execute_sql('ALTER TABLE "widgets" ADD COLUMN "color" TEXT')
        database.execute_sql(
            'CREATE TABLE "gadgets" ('
            '"id" INTEGER NOT NULL PRIMARY KEY, '
            '"widget_id" INTEGER NOT NULL, '
            'FOREIGN KEY ("widget_id") REFERENCES "widgets" ("id"))'
        )

    return (
        Migration(version=1, name="001_widgets", apply=widget_v1),
        Migration(version=2, name="002_widget_gadgets", apply=widget_v2),
    )


def _add_widget_notes(database: Database) -> None:
    database.execute_sql('ALTER TABLE "widgets" ADD COLUMN "notes" TEXT')


def _list_tables(manager: DatabaseManager) -> set[str]:
    return set(manager.database.get_tables())


def test_empty_database_reaches_latest_schema(tmp_path: Path) -> None:
    """Пустая БД получает все таблицы схемы и запись о применённой версии."""
    manager = open_database(_database_path(tmp_path))
    try:
        assert current_schema_version(manager.database) == LATEST_SCHEMA_VERSION
        assert EXPECTED_TABLES <= _list_tables(manager)
        assert applied_migrations(manager.database) == {
            migration.version: migration.name for migration in MIGRATIONS
        }
    finally:
        manager.close()


def test_repeated_apply_is_noop(tmp_path: Path) -> None:
    """Повторный запуск миграций не применяет ничего и не переписывает историю."""
    manager = open_database(_database_path(tmp_path))
    try:
        before = applied_migrations(manager.database)
        outcome = manager.migrate()

        assert outcome.applied == ()
        assert outcome.previous_version == LATEST_SCHEMA_VERSION
        assert outcome.current_version == LATEST_SCHEMA_VERSION
        assert applied_migrations(manager.database) == before
    finally:
        manager.close()


def test_migrate_after_close_does_not_autoconnect(tmp_path: Path) -> None:
    """`migrate()` на закрытом менеджере не открывает Peewee и не меняет схему."""
    path = _database_path(tmp_path)
    manager = open_database(path)
    manager.close()

    with pytest.raises(DatabaseClosedError):
        manager.migrate()

    assert manager._database.is_closed()
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM "schema_migrations"').fetchone() == (1,)

    try:
        outcome = manager.open()
        assert outcome.applied == ()
        assert manager.is_open
    finally:
        manager.close()


def test_previous_schema_upgrades_and_preserves_data(tmp_path: Path) -> None:
    """Синтетическая v1 обновляется до v2; строки v1 остаются, новые объекты есть."""
    path = _database_path(tmp_path)
    chain = _synthetic_previous_schema()

    manager = DatabaseManager(path)
    manager.connect()
    try:
        first = apply_migrations(manager.database, chain[:1])
        assert first.applied == (1,)
        manager.database.execute_sql(
            'INSERT INTO "widgets" ("id", "name") VALUES (1, ?)', ("первый",)
        )
    finally:
        manager.close()

    reopened = DatabaseManager(path)
    reopened.connect()
    try:
        second = apply_migrations(reopened.database, chain)

        assert second.previous_version == 1
        assert second.current_version == 2
        assert second.applied == (2,)
        assert reopened.database.execute_sql(
            'SELECT "name", "color" FROM "widgets" WHERE "id" = 1'
        ).fetchone() == ("первый", None)
        assert "gadgets" in set(reopened.database.get_tables())
        assert current_schema_version(reopened.database) == 2
    finally:
        reopened.close()


def test_open_database_is_idempotent_across_reopen(tmp_path: Path) -> None:
    """Данные переживают закрытие и повторное открытие: схема уже актуальна."""
    path = _database_path(tmp_path)

    first = open_database(path)
    try:
        first.database.execute_sql(
            'INSERT INTO "jobs" ("kind", "status", "provider_id", "model_id", "created_at") '
            "VALUES (?, ?, ?, ?, ?)",
            ("image_generation", "created", "polza", "synthetic-model", "2026-01-01T00:00:00Z"),
        )
    finally:
        first.close()

    second = open_database(path)
    try:
        row = second.database.execute_sql('SELECT COUNT(*) FROM "jobs"').fetchone()
        assert row == (1,)
        assert applied_migrations(second.database) == {1: "001_initial"}
    finally:
        second.close()


def test_foreign_keys_are_enabled_and_enforced(tmp_path: Path) -> None:
    """`PRAGMA foreign_keys` включён, поэтому ссылка на несуществующий Job отклоняется."""
    manager = open_database(_database_path(tmp_path))
    try:
        assert manager.database.execute_sql("PRAGMA foreign_keys").fetchone() == (1,)
        assert manager.database.execute_sql("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        with pytest.raises(IntegrityError):
            manager.database.execute_sql(
                'INSERT INTO "prompt_sources" '
                '("job_id", "kind", "position", "text_snapshot") VALUES (?, ?, ?, ?)',
                (9999, "cli_arg", 0, "текст"),
            )
    finally:
        manager.close()


def test_cascade_delete_follows_declared_foreign_key(tmp_path: Path) -> None:
    """Удаление Job уносит его prompt sources: FK объявлен с `ON DELETE CASCADE`."""
    manager = open_database(_database_path(tmp_path))
    try:
        manager.database.execute_sql(
            'INSERT INTO "jobs" ("id", "kind", "status", "provider_id", "model_id", "created_at") '
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "image_generation", "created", "polza", "synthetic-model", "2026-01-01T00:00:00Z"),
        )
        manager.database.execute_sql(
            'INSERT INTO "prompt_sources" ("job_id", "kind", "position", "text_snapshot") '
            "VALUES (?, ?, ?, ?)",
            (1, "cli_arg", 0, "текст"),
        )
        manager.database.execute_sql('DELETE FROM "jobs" WHERE "id" = 1')

        assert manager.database.execute_sql('SELECT COUNT(*) FROM "prompt_sources"').fetchone() == (
            0,
        )
    finally:
        manager.close()


def test_schema_newer_than_code_fails_closed_without_downgrade(tmp_path: Path) -> None:
    """База «из будущего» не понижается: поднимается `SchemaTooNewError`, данные целы."""
    manager = open_database(_database_path(tmp_path))
    try:
        manager.database.execute_sql(
            'INSERT INTO "schema_migrations" ("version", "name", "applied_at") VALUES (?, ?, ?)',
            (LATEST_SCHEMA_VERSION + 1, "999_from_future", "2026-01-01T00:00:00Z"),
        )

        with pytest.raises(SchemaTooNewError) as excinfo:
            manager.migrate()

        assert excinfo.value.database_version == LATEST_SCHEMA_VERSION + 1
        assert excinfo.value.code_version == LATEST_SCHEMA_VERSION
        # Ни одна запись реальной истории не удалена и не переписана.
        assert applied_migrations(manager.database) == {
            1: "001_initial",
            LATEST_SCHEMA_VERSION + 1: "999_from_future",
        }
        assert EXPECTED_TABLES <= _list_tables(manager)
    finally:
        manager.close()


def test_broken_migration_history_is_rejected(tmp_path: Path) -> None:
    """Разрыв в истории версий — ошибка, а не молчаливое «продолжим с конца»."""
    chain = (
        *_synthetic_previous_schema(),
        Migration(version=3, name="003_widget_notes", apply=_add_widget_notes),
    )

    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        apply_migrations(manager.database, chain[:2])
        # HISTORY: версия 1 потеряна, но версия 2 записана.
        manager.database.execute_sql('DELETE FROM "schema_migrations" WHERE "version" = 1')

        with pytest.raises(MigrationDefinitionError):
            apply_migrations(manager.database, chain)
    finally:
        manager.close()


def test_validate_migrations_rejects_duplicates_gaps_and_empty_names() -> None:
    """Набор миграций обязан быть 1..N без дублей, пропусков и безымянных шагов."""

    def noop(database: Database) -> None:  # pragma: no cover - тело не вызывается
        raise AssertionError("миграция не должна применяться")

    good = (Migration(1, "001", noop), Migration(2, "002", noop))
    assert validate_migrations(good) == good

    with pytest.raises(MigrationDefinitionError):
        validate_migrations(())
    with pytest.raises(MigrationDefinitionError):
        validate_migrations((Migration(1, "001", noop), Migration(1, "001_dup", noop)))
    with pytest.raises(MigrationDefinitionError):
        validate_migrations((Migration(1, "001", noop), Migration(3, "003", noop)))
    with pytest.raises(MigrationDefinitionError):
        validate_migrations((Migration(1, "", noop),))


def test_connection_is_not_shared_between_threads(tmp_path: Path) -> None:
    """`check_same_thread=False` не включён: чужой поток получает явный отказ."""
    manager = open_database(_database_path(tmp_path))
    try:
        assert "check_same_thread" not in manager.database.connect_params

        ownership_errors: list[Exception] = []

        def use_storage() -> None:
            try:
                manager.migrate()
            except DatabaseOwnershipError as exc:
                ownership_errors.append(exc)

        thread = threading.Thread(target=use_storage)
        thread.start()
        thread.join()

        assert len(ownership_errors) == 1

        # Драйвер тоже не разрешает передачу соединения: guard стоит не только в
        # обёртке, а `check_same_thread` остаётся значением по умолчанию (True).
        connection = manager.connection
        driver_errors: list[Exception] = []

        def use_connection() -> None:
            try:
                connection.execute("SELECT 1")
            except sqlite3.ProgrammingError as exc:
                driver_errors.append(exc)

        raw_thread = threading.Thread(target=use_connection)
        raw_thread.start()
        raw_thread.join()

        assert len(driver_errors) == 1
    finally:
        manager.close()


def test_missing_parent_directory_is_not_created_silently(tmp_path: Path) -> None:
    """Storage не создаёт data-root сам: отсутствующий каталог — явный отказ."""
    missing = tmp_path / "absent" / DATABASE_FILENAME
    manager = DatabaseManager(missing)
    with pytest.raises(FileNotFoundError):
        manager.connect()
    assert not missing.parent.exists()


def test_pragmas_are_applied_to_every_connection(tmp_path: Path) -> None:
    """Pragmas из `default_pragmas` действуют после повторного подключения."""
    path = _database_path(tmp_path)
    assert default_pragmas() == (
        ("foreign_keys", "ON"),
        ("journal_mode", "WAL"),
        ("busy_timeout", str(5000)),
    )
    manager = DatabaseManager(path)
    try:
        manager.connect()
        assert manager.database.execute_sql("PRAGMA foreign_keys").fetchone() == (1,)
        assert manager.database.execute_sql("PRAGMA busy_timeout").fetchone() == (5000,)
        manager.close()

        manager.connect()
        assert manager.database.execute_sql("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        manager.close()


def test_installed_engine_versions_are_verified_and_pinned(tmp_path: Path) -> None:
    """Установленный Peewee входит в `>=3.19,<4`; версии видны после `connect`."""
    versions = verify_engine_versions()

    assert parse_version(versions.peewee)[:2] >= (3, 19)
    assert parse_version(versions.peewee)[0] < 4
    assert len(parse_version(versions.sqlite)) >= 3

    manager = DatabaseManager(_database_path(tmp_path))
    try:
        manager.connect()
        assert manager.engine_versions == versions
    finally:
        manager.close()

    assert manager.engine_versions is None


def test_engine_outside_pinned_range_is_rejected() -> None:
    """Непроверенная версия ORM — явный отказ, а не тихий запуск миграций."""
    assert parse_version("3.19.0rc1") == (3, 19, 0)
    assert parse_version("3") == (3,)

    with pytest.raises(StorageError):
        verify_engine_versions(peewee_version="3.18.4")
    with pytest.raises(StorageError):
        verify_engine_versions(peewee_version="4.0.0")
    with pytest.raises(StorageError):
        verify_engine_versions(peewee_version="не-версия")
    with pytest.raises(StorageError):
        verify_engine_versions(sqlite_version="3.45")

    ok = verify_engine_versions(peewee_version="3.19.0", sqlite_version="3.45.1")
    assert ok.peewee == "3.19.0"
    assert ok.sqlite == "3.45.1"


def test_close_without_connection_is_safe(tmp_path: Path) -> None:
    """Закрытие неоткрытого менеджера не поднимает ошибку и не создаёт файл БД."""
    path = _database_path(tmp_path)
    manager = DatabaseManager(path)
    manager.close()
    assert not path.exists()


def test_open_closes_connection_when_migration_fails(tmp_path: Path) -> None:
    """`open()` при отказе миграции не оставляет открытое соединение висеть."""
    path = _database_path(tmp_path)
    seeding = open_database(path)
    try:
        seeding.database.execute_sql(
            'INSERT INTO "schema_migrations" ("version", "name", "applied_at") VALUES (?, ?, ?)',
            (LATEST_SCHEMA_VERSION + 1, "999_from_future", "2026-01-01T00:00:00Z"),
        )
    finally:
        seeding.close()

    manager = DatabaseManager(path)
    assert manager.path == path
    with pytest.raises(SchemaTooNewError):
        manager.open()
    assert manager.is_open is False


def test_manager_context_manager_opens_and_closes(tmp_path: Path) -> None:
    """`with DatabaseManager(...)` открывает схему и закрывает соединение на выходе."""
    manager = DatabaseManager(_database_path(tmp_path))
    with manager as opened:
        assert opened is manager
        assert opened.is_open is True
        assert current_schema_version(opened.database) == LATEST_SCHEMA_VERSION

    assert manager.is_open is False

    # Повторный `connect` на уже открытом соединении — no-op, а не второе соединение.
    manager.connect()
    try:
        connection = manager.connection
        assert manager.connect() is None
        assert manager.connection is connection
    finally:
        manager.close()


def test_unwritable_database_directory_surfaces_as_database_error(tmp_path: Path) -> None:
    """Сбой открытия файла не превращается в успешное «пустое» состояние."""
    manager = DatabaseManager(tmp_path)  # путь — каталог, а не файл
    with pytest.raises(OperationalError):
        manager.connect()
    assert manager.is_open is False
