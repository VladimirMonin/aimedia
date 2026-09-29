"""Безопасные события storage: открытие БД, миграции и история Job (E04, C06c1).

События E04 (`docs/plans/README.md`): `database_opened`, `migration_started/
completed/failed`, `job_created`, `job_state_changed`, `remote_ref_saved`.
Проверяется не только факт записи, но и её границы: в событии есть ID, статусы,
версии и счётчики, но нет текста prompt, путей, SQL и сообщений драйвера.

Все базы живут в `tmp_path`; реальная сеть и пользовательский data-root не
используются. События сверяются с фактическим состоянием SQLite: номер версии
события совпадает с `schema_migrations`, а отказ миграции действительно откачен.
"""

from __future__ import annotations

import io
import json
import sqlite3
import traceback
from datetime import UTC, datetime
from pathlib import Path

import peewee
import pytest
from peewee import Database

from aimedia.domain import (
    CompiledPrompt,
    ImageGenerationRequest,
    Job,
    JobKind,
    JobStatus,
    ModelRef,
    PromptSource,
    PromptSourceKind,
    ProviderRef,
    RemoteJobRef,
    RemoteOperation,
)
from aimedia.logging import EVENT_FIELDS, EventLogger, correlation
from aimedia.storage import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    DatabaseBusyError,
    DatabaseManager,
    InvalidStoredJobStatusError,
    Migration,
    MigrationFailedError,
    NestedStorageTransactionError,
    PeeweeJobRepository,
    StorageError,
    applied_migrations,
    apply_migrations,
    current_schema_version,
    is_database_busy,
    log_database_opened,
    log_job_created,
    log_job_state_changed,
    log_migration_failed,
    log_remote_ref_saved,
    open_database,
)

DATABASE_FILENAME = "database.sqlite3"
CREATED_AT = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)

PROVIDER = ProviderRef(id="polza")
# Модели с таким id нет в Registry: события не должны зависеть от его загрузки.
MODEL = ModelRef(id="synthetic-events-only-model")

# Canary-значения: если они появятся в записи, границы события нарушены.
CANARY_PROMPT = "SECRET-PROMPT-CANARY-7c1d"
CANARY_SOURCE_PATH = Path("prompts/canary-events-source.md")
CANARY_REMOTE_JOB_ID = f"secret-ref-{CANARY_PROMPT}-{CANARY_SOURCE_PATH.as_posix()}-raw-token"
CANARY_MIGRATION_NAME = f"migration-{CANARY_PROMPT}-{CANARY_SOURCE_PATH.as_posix()}"
CANARY_PROVIDER_ID = f"provider-{CANARY_PROMPT}-{CANARY_SOURCE_PATH.as_posix()}"


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILENAME


def _logger() -> tuple[EventLogger, io.StringIO]:
    stream = io.StringIO()
    return EventLogger(stream=stream, min_level="DEBUG"), stream


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _events(stream: io.StringIO) -> list[str]:
    return [str(record["event"]) for record in _records(stream)]


def _details(stream: io.StringIO, event: str) -> dict[str, object]:
    """Details единственной записи события: его отсутствие — ошибка теста."""
    matches = [record for record in _records(stream) if record["event"] == event]
    assert len(matches) == 1, f"ожидалась ровно одна запись {event}: {_records(stream)}"
    return dict(matches[0].get("details") or {})


def _assert_allowed_fields(stream: io.StringIO) -> None:
    """Каждая запись состоит только из разрешённых полей контракта логирования."""
    for record in _records(stream):
        assert set(record) <= set(EVENT_FIELDS), record


def _failing_chain(*, failed_name: str = "002_beta") -> tuple[Migration, ...]:
    """v1 создаёт таблицу, v2 создаёт вторую и падает — частичный DDL обязан исчезнуть."""

    def good_v1(database: Database) -> None:
        database.execute_sql(
            'CREATE TABLE "alpha" ("id" INTEGER NOT NULL PRIMARY KEY, "name" TEXT NOT NULL)'
        )

    def broken_v2(database: Database) -> None:
        database.execute_sql('CREATE TABLE "beta" ("id" INTEGER NOT NULL PRIMARY KEY)')
        raise RuntimeError(f"синтетический сбой миграции v2 {CANARY_PROMPT}")

    return (
        Migration(version=1, name="001_alpha", apply=good_v1),
        Migration(version=2, name=failed_name, apply=broken_v2),
    )


def _created_job() -> Job:
    """Новый Job истории с canary-prompt и путём источника."""
    prompt = CompiledPrompt(text=CANARY_PROMPT, source_count=1)
    return Job(
        kind=JobKind.IMAGE_GENERATE,
        status=JobStatus.CREATED,
        provider=PROVIDER,
        model=MODEL,
        request=ImageGenerationRequest(provider=PROVIDER, model=MODEL, prompt=prompt),
        prompt_sources=[
            PromptSource(
                kind=PromptSourceKind.FILE,
                text=CANARY_PROMPT,
                position=0,
                path=CANARY_SOURCE_PATH,
            )
        ],
        compiled_prompt=prompt,
        created_at=CREATED_AT,
    )


def _running_job(job_id: int, *, remote_ref: RemoteJobRef | None) -> Job:
    """Тот же Job истории в статусе `running`, при необходимости с remote ref."""
    return _created_job().model_copy(
        update={
            "id": job_id,
            "status": JobStatus.RUNNING,
            "started_at": CREATED_AT,
            "remote_ref": remote_ref,
        }
    )


def test_database_opened_reports_schema_and_engine_versions_without_path(
    tmp_path: Path,
) -> None:
    """`database_opened` несёт версии схемы/движка, но не путь к файлу БД."""
    path = _database_path(tmp_path)
    logger, stream = _logger()

    manager = open_database(path, logger=logger)
    manager.close()

    assert _events(stream)[-1] == "database_opened"
    details = _details(stream, "database_opened")
    assert details["schema_version"] == LATEST_SCHEMA_VERSION
    assert details["peewee_version"] == peewee.__version__
    assert details["sqlite_version"] == sqlite3.sqlite_version
    # Путь к БД и её имя не попадают в запись: диагностика не раскрывает диск.
    assert str(path) not in stream.getvalue()
    assert DATABASE_FILENAME not in stream.getvalue()
    _assert_allowed_fields(stream)


def test_fresh_database_logs_migration_started_then_completed(tmp_path: Path) -> None:
    """Пустая БД пишет `migration_started` до транзакции и `completed` после commit."""
    logger, stream = _logger()

    manager = open_database(_database_path(tmp_path), logger=logger)
    try:
        assert _events(stream) == ["migration_started", "migration_completed", "database_opened"]
        assert _details(stream, "migration_started") == {
            "previous_version": 0,
            "target_version": LATEST_SCHEMA_VERSION,
            "pending_migrations": LATEST_SCHEMA_VERSION,
        }
        assert _details(stream, "migration_completed") == {
            "previous_version": 0,
            "current_version": LATEST_SCHEMA_VERSION,
            "applied": list(range(1, LATEST_SCHEMA_VERSION + 1)),
        }
        # `completed` сверяется с фактической историей, а не только с событием.
        assert applied_migrations(manager.database) == {
            migration.version: migration.name for migration in MIGRATIONS
        }
        assert current_schema_version(manager.database) == LATEST_SCHEMA_VERSION
    finally:
        manager.close()

    _assert_allowed_fields(stream)


def test_reopened_database_writes_no_migration_events(tmp_path: Path) -> None:
    """Прогон без ожидающих шагов молчит: повторный запуск миграций — no-op."""
    path = _database_path(tmp_path)
    seeding = open_database(path)
    seeding.close()

    logger, stream = _logger()
    manager = open_database(path, logger=logger)
    try:
        assert _events(stream) == ["database_opened"]
        assert _details(stream, "database_opened")["schema_version"] == LATEST_SCHEMA_VERSION
    finally:
        manager.close()


def test_migration_failed_reports_step_without_error_text_or_sql(tmp_path: Path) -> None:
    """`migration_failed` несёт номер шага, но не имя, ошибку или SQL."""
    logger, stream = _logger()
    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        with pytest.raises(MigrationFailedError):
            apply_migrations(
                manager.database, _failing_chain(failed_name=CANARY_MIGRATION_NAME), logger=logger
            )

        assert _events(stream) == ["migration_started", "migration_failed"]
        failed = _records(stream)[1]
        assert failed["level"] == "ERROR"
        assert failed["details"] == {"version": 2}
        # Отказ действительно откачен: частичной таблицы и ложной версии нет.
        assert "beta" not in set(manager.database.get_tables())
        assert current_schema_version(manager.database) == 1
    finally:
        manager.close()

    # Ни SQL, ни текст исходного исключения, ни canary-prompt в записи не попали.
    output = stream.getvalue()
    assert "CREATE TABLE" not in output
    assert "синтетический сбой" not in output
    assert CANARY_PROMPT not in output
    assert CANARY_MIGRATION_NAME not in output
    assert str(CANARY_SOURCE_PATH) not in output
    _assert_allowed_fields(stream)


def test_logging_is_optional_for_storage_operations(tmp_path: Path) -> None:
    """Без `logger` storage работает молча: ни открытие, ни миграции, ни save не падают."""
    manager = open_database(_database_path(tmp_path))
    try:
        saved = PeeweeJobRepository(manager).save(_created_job())
        assert saved.id is not None
        assert manager.migrate().applied == ()
    finally:
        manager.close()


def test_job_event_helpers_are_silent_without_logger() -> None:
    """Job-события без логгера — no-op: storage работает без диагностического канала."""
    assert log_job_created(None, job_id=1, kind="image.generate", status="created") is None
    assert (
        log_job_state_changed(None, job_id=1, previous_status="created", current_status="running")
        is None
    )
    assert (
        log_remote_ref_saved(
            None,
            job_id=1,
            operation=RemoteOperation.MEDIA,
        )
        is None
    )
    # Отсутствующий диагностический канал остаётся no-op и для недоверенного ввода.
    assert log_job_created(None, job_id=1, kind=CANARY_PROMPT, status=CANARY_PROMPT) is None
    assert (
        log_job_state_changed(
            None, job_id=1, previous_status=CANARY_PROMPT, current_status=CANARY_PROMPT
        )
        is None
    )
    assert log_remote_ref_saved(None, job_id=1, operation=CANARY_PROMPT) is None


@pytest.mark.parametrize("field", ["kind", "status"])
def test_log_job_created_rejects_tainted_direct_value(field: str) -> None:
    """Прямой вызов не выпускает произвольный kind/status даже с child/context."""
    logger, stream = _logger()
    child = logger.child(command=CANARY_PROMPT, provider=CANARY_PROVIDER_ID)
    arguments = {"kind": JobKind.IMAGE_GENERATE, "status": JobStatus.CREATED}
    arguments[field] = CANARY_PROMPT

    with correlation(job_id=CANARY_REMOTE_JOB_ID, remote_job_id=CANARY_REMOTE_JOB_ID):
        with pytest.raises(ValueError) as caught:
            log_job_created(child, job_id=17, **arguments)

    assert _records(stream) == []
    assert CANARY_PROMPT not in "".join(traceback.format_exception(caught.value))
    assert CANARY_REMOTE_JOB_ID not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("field", ["previous_status", "current_status"])
def test_log_job_state_changed_rejects_tainted_direct_value(field: str) -> None:
    """Обе стороны перехода проверяются до записи события."""
    logger, stream = _logger()
    child = logger.child(command=CANARY_PROMPT, job_id=CANARY_REMOTE_JOB_ID)
    arguments = {"previous_status": JobStatus.CREATED, "current_status": JobStatus.RUNNING}
    arguments[field] = CANARY_PROMPT

    with correlation(provider=CANARY_PROVIDER_ID, remote_operation=CANARY_REMOTE_JOB_ID):
        with pytest.raises(ValueError) as caught:
            log_job_state_changed(child, job_id=17, **arguments)

    assert _records(stream) == []
    assert CANARY_PROMPT not in "".join(traceback.format_exception(caught.value))
    assert CANARY_REMOTE_JOB_ID not in "".join(traceback.format_exception(caught.value))


def test_log_remote_ref_saved_rejects_tainted_direct_operation() -> None:
    """Аннотация RemoteOperation не заменяет runtime-проверку raw string."""
    logger, stream = _logger()
    child = logger.child(remote_operation=CANARY_PROMPT, provider=CANARY_PROVIDER_ID)

    with correlation(remote_job_id=CANARY_REMOTE_JOB_ID, command=CANARY_PROMPT):
        with pytest.raises(ValueError) as caught:
            log_remote_ref_saved(child, job_id=17, operation=CANARY_PROMPT)

    assert _records(stream) == []
    failure = "".join(traceback.format_exception(caught.value))
    assert CANARY_PROMPT not in failure
    assert CANARY_REMOTE_JOB_ID not in failure


@pytest.mark.parametrize("helper", ["created", "changed", "remote_ref"])
def test_direct_job_helpers_reject_tainted_job_id(helper: str) -> None:
    """Аннотация int не разрешает прямому вызову писать произвольный job_id."""
    logger, stream = _logger()
    child = logger.child(command=CANARY_PROMPT, remote_job_id=CANARY_REMOTE_JOB_ID)
    with correlation(provider=CANARY_PROVIDER_ID, job_id=CANARY_PROMPT):
        with pytest.raises(ValueError) as caught:
            if helper == "created":
                log_job_created(
                    child, job_id=CANARY_PROMPT, kind=JobKind.IMAGE_GENERATE, status="created"
                )
            elif helper == "changed":
                log_job_state_changed(
                    child, job_id=CANARY_PROMPT, previous_status="created", current_status="running"
                )
            else:
                log_remote_ref_saved(child, job_id=CANARY_PROMPT, operation=RemoteOperation.MEDIA)

    assert _records(stream) == []
    failure = "".join(traceback.format_exception(caught.value))
    assert CANARY_PROMPT not in failure
    assert CANARY_REMOTE_JOB_ID not in failure


def test_direct_job_helpers_accept_enums_and_canonical_strings_without_correlation() -> None:
    """Разрешённые значения сохраняют нормальную диагностику и безопасную корреляцию."""
    logger, stream = _logger()
    child = logger.child(command=CANARY_PROMPT, provider=CANARY_PROVIDER_ID)

    with correlation(remote_job_id=CANARY_REMOTE_JOB_ID, job_id=CANARY_PROMPT):
        log_job_created(child, job_id=17, kind=JobKind.IMAGE_GENERATE, status="created")
        log_job_state_changed(
            child, job_id=17, previous_status="created", current_status=JobStatus.RUNNING
        )
        log_remote_ref_saved(child, job_id=17, operation="media")

    assert _events(stream) == ["job_created", "job_state_changed", "remote_ref_saved"]
    assert _details(stream, "job_created") == {"kind": "image.generate", "status": "created"}
    assert _details(stream, "job_state_changed") == {
        "previous_status": "created",
        "current_status": "running",
    }
    assert _records(stream)[2]["remote_operation"] == "media"
    assert all(record["job_id"] == "17" for record in _records(stream))
    assert CANARY_PROMPT not in stream.getvalue()
    assert CANARY_REMOTE_JOB_ID not in stream.getvalue()
    assert CANARY_PROVIDER_ID not in stream.getvalue()


def _operational_error(*, code: int | None = None, message: str = "driver failure") -> Exception:
    """Peewee-ошибка драйвера с той же цепочкой, что при настоящем отказе.

    Peewee поднимает свой `OperationalError` внутри `except`, поэтому исходный
    `sqlite3.OperationalError` оказывается в `__context__`; `sqlite_errorcode` есть
    только у исходного исключения.
    """
    cause = sqlite3.OperationalError(message)
    if code is not None:
        cause.sqlite_errorcode = code
    wrapped = peewee.OperationalError(cause, message)
    wrapped.__context__ = cause
    return wrapped


def test_is_database_busy_discriminates_only_busy_and_locked() -> None:
    """Ожидаемая блокировка распознаётся по коду/тексту, прочие отказы драйвера — нет."""
    # SQLITE_BUSY (5) и SQLITE_LOCKED (6), включая расширенные коды: младший байт.
    assert is_database_busy(_operational_error(code=5)) is True
    assert is_database_busy(_operational_error(code=6)) is True
    assert is_database_busy(_operational_error(code=0x105)) is True
    # Fallback: драйвер без `sqlite_errorcode` сообщает блокировку текстом.
    assert is_database_busy(_operational_error(message="database is locked")) is True
    # Прочие отказы драйвера не подменяются управляемым busy-исходом.
    assert is_database_busy(_operational_error(code=1, message="no such table: jobs")) is False
    assert is_database_busy(RuntimeError("не SQLite")) is False


def test_database_busy_error_is_storage_error_not_domain_error() -> None:
    """`DatabaseBusyError` — локальный отказ storage, не отказ provider/домена."""
    error = DatabaseBusyError()

    assert isinstance(error, StorageError)
    assert str(error)
    # Сообщение описывает откат целиком и не притворяется доменной ошибкой Job.
    assert not hasattr(error, "to_job_error")


def test_database_opened_omits_unknown_engine_versions() -> None:
    """Неизвестные версии движка не превращаются в запись с пустым значением."""
    logger, stream = _logger()

    log_database_opened(logger, schema_version=LATEST_SCHEMA_VERSION)

    assert _details(stream, "database_opened") == {"schema_version": LATEST_SCHEMA_VERSION}
    _assert_allowed_fields(stream)


def test_save_with_unknown_id_writes_no_event(tmp_path: Path) -> None:
    """Save с неизвестным ID — отказ до событий: записанного факта не было."""
    logger, stream = _logger()
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        with pytest.raises(ValueError):
            repository.save(_created_job().model_copy(update={"id": 999}), logger=logger)
    finally:
        manager.close()

    assert _records(stream) == []


def test_job_created_reports_identity_without_prompt_or_paths(tmp_path: Path) -> None:
    """`job_created` несёт локальный ID, kind и статус, но не prompt и не пути."""
    logger, stream = _logger()
    manager = open_database(_database_path(tmp_path))
    try:
        saved = PeeweeJobRepository(manager).save(_created_job(), logger=logger)
    finally:
        manager.close()

    assert saved.id is not None
    assert _events(stream) == ["job_created"]
    created = _records(stream)[0]
    assert created["job_id"] == str(saved.id)
    assert created["details"] == {"kind": "image.generate", "status": "created"}
    output = stream.getvalue()
    assert CANARY_PROMPT not in output
    assert str(CANARY_SOURCE_PATH) not in output
    assert CANARY_SOURCE_PATH.name not in output
    _assert_allowed_fields(stream)


def test_job_state_changed_reports_status_pair_and_is_not_repeated(tmp_path: Path) -> None:
    """Переход статуса пишет пару статусов; повторное сохранение её не дублирует."""
    logger, stream = _logger()
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_created_job(), logger=logger).id
        assert job_id is not None

        running = repository.save(_running_job(job_id, remote_ref=None), logger=logger)
        repository.save(running, logger=logger)
    finally:
        manager.close()

    assert _events(stream) == ["job_created", "job_state_changed"]
    changed = _records(stream)[1]
    assert changed["job_id"] == str(job_id)
    assert changed["details"] == {"previous_status": "created", "current_status": "running"}
    assert CANARY_PROMPT not in stream.getvalue()
    _assert_allowed_fields(stream)


def test_save_rejects_tainted_stored_status_without_write_or_event(tmp_path: Path) -> None:
    """Повреждённый статус строки не попадает в событие или текст исключения."""
    logger, stream = _logger()
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_created_job()).id
        assert job_id is not None
        tainted_status = f"{CANARY_PROMPT}-{CANARY_SOURCE_PATH.as_posix()}-raw-token"
        manager.database.execute_sql(
            'UPDATE "jobs" SET "status" = ? WHERE "id" = ?',
            (tainted_status, job_id),
        )
        before = manager.database.execute_sql(
            'SELECT * FROM "jobs" WHERE "id" = ?', (job_id,)
        ).fetchone()
        children_before = {
            table: manager.database.execute_sql(
                f'SELECT * FROM "{table}" WHERE "job_id" = ?', (job_id,)
            ).fetchall()
            for table in ("prompt_sources", "inputs", "artifacts")
        }

        with pytest.raises(InvalidStoredJobStatusError) as caught:
            repository.save(_running_job(job_id, remote_ref=None), logger=logger)

        assert (
            manager.database.execute_sql(
                'SELECT * FROM "jobs" WHERE "id" = ?', (job_id,)
            ).fetchone()
            == before
        )
        for table, rows in children_before.items():
            assert (
                manager.database.execute_sql(
                    f'SELECT * FROM "{table}" WHERE "job_id" = ?', (job_id,)
                ).fetchall()
                == rows
            )
    finally:
        manager.close()

    assert _records(stream) == []
    failure = "".join(traceback.format_exception(caught.value))
    for canary in (CANARY_PROMPT, CANARY_SOURCE_PATH.as_posix(), "raw-token"):
        assert canary not in stream.getvalue()
        assert canary not in failure


def test_remote_ref_saved_reports_operation_and_is_not_repeated(tmp_path: Path) -> None:
    """`remote_ref_saved` несёт ID и тип операции; та же ссылка не дублируется."""
    logger, stream = _logger()
    remote_ref = RemoteJobRef(
        provider_id=CANARY_PROVIDER_ID,
        remote_job_id=CANARY_REMOTE_JOB_ID,
        operation=RemoteOperation.MEDIA,
    )
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        job_id = repository.save(_created_job(), logger=logger).id
        assert job_id is not None

        with_ref = repository.save(_running_job(job_id, remote_ref=remote_ref), logger=logger)
        repository.save(with_ref, logger=logger)
    finally:
        manager.close()

    assert _events(stream) == ["job_created", "job_state_changed", "remote_ref_saved"]
    saved = _records(stream)[2]
    assert saved["job_id"] == str(job_id)
    assert "provider" not in saved
    assert "remote_job_id" not in saved
    assert saved["remote_operation"] == RemoteOperation.MEDIA.value
    assert "details" not in saved
    output = stream.getvalue()
    assert CANARY_PROMPT not in output
    assert str(CANARY_SOURCE_PATH) not in output
    assert CANARY_REMOTE_JOB_ID not in output
    assert CANARY_PROVIDER_ID not in output
    _assert_allowed_fields(stream)


def test_storage_events_omit_tainted_remote_id_from_child_and_correlation(
    tmp_path: Path,
) -> None:
    """Raw поля миграции/ref и child/context не проходят в storage events."""
    logger, stream = _logger()
    child = logger.child(
        command=CANARY_MIGRATION_NAME,
        job_id=CANARY_MIGRATION_NAME,
        provider=CANARY_PROVIDER_ID,
        remote_operation=CANARY_MIGRATION_NAME,
        remote_job_id=CANARY_REMOTE_JOB_ID,
    )
    remote_ref = RemoteJobRef(
        provider_id=CANARY_PROVIDER_ID,
        remote_job_id=CANARY_REMOTE_JOB_ID,
        operation=RemoteOperation.MEDIA,
    )
    with correlation(
        command=CANARY_PROVIDER_ID,
        job_id=CANARY_PROVIDER_ID,
        provider=CANARY_MIGRATION_NAME,
        remote_operation=CANARY_PROVIDER_ID,
        remote_job_id=CANARY_REMOTE_JOB_ID,
    ):
        manager = open_database(_database_path(tmp_path), logger=child)
        try:
            repository = PeeweeJobRepository(manager)
            job_id = repository.save(_created_job(), logger=child).id
            assert job_id is not None
            repository.save(_running_job(job_id, remote_ref=remote_ref), logger=child)
            log_migration_failed(child, version=2)
        finally:
            manager.close()

    assert _events(stream) == [
        "migration_started",
        "migration_completed",
        "database_opened",
        "job_created",
        "job_state_changed",
        "remote_ref_saved",
        "migration_failed",
    ]
    assert all("remote_job_id" not in record for record in _records(stream))
    assert all("command" not in record and "provider" not in record for record in _records(stream))
    assert all(
        "remote_operation" not in record or record["remote_operation"] == "media"
        for record in _records(stream)
    )
    output = stream.getvalue()
    assert CANARY_REMOTE_JOB_ID not in output
    assert CANARY_MIGRATION_NAME not in output
    assert CANARY_PROVIDER_ID not in output
    assert CANARY_PROMPT not in output
    assert str(CANARY_SOURCE_PATH) not in output
    assert "raw-token" not in output
    _assert_allowed_fields(stream)


def test_remote_ref_without_operation_does_not_inherit_tainted_operation() -> None:
    """Отсутствующий enum не заменяется строкой из child/context корреляции."""
    logger, stream = _logger()
    child = logger.child(remote_operation=CANARY_PROVIDER_ID, provider=CANARY_PROVIDER_ID)
    with correlation(remote_operation=CANARY_MIGRATION_NAME):
        log_remote_ref_saved(child, job_id=17, operation=None)

    assert _events(stream) == ["remote_ref_saved"]
    assert _records(stream)[0]["job_id"] == "17"
    assert "remote_operation" not in _records(stream)[0]
    assert "provider" not in _records(stream)[0]
    assert CANARY_PROVIDER_ID not in stream.getvalue()
    assert CANARY_MIGRATION_NAME not in stream.getvalue()


def test_save_in_outer_transaction_fails_without_success_event_or_row(tmp_path: Path) -> None:
    """Внешний rollback не может отменить Job после преждевременного `job_created`."""
    logger, stream = _logger()
    manager = open_database(_database_path(tmp_path))
    try:
        repository = PeeweeJobRepository(manager)
        with pytest.raises(RuntimeError, match="rollback sentinel"):
            with manager.database.atomic():
                with pytest.raises(NestedStorageTransactionError, match="save"):
                    repository.save(_created_job(), logger=logger)
                assert manager.database.execute_sql('SELECT count(*) FROM "jobs"').fetchone() == (
                    0,
                )
                raise RuntimeError("rollback sentinel")
        assert manager.database.execute_sql('SELECT count(*) FROM "jobs"').fetchone() == (0,)
        assert _records(stream) == []
        assert repository.save(_created_job(), logger=logger).id is not None
        assert _events(stream) == ["job_created"]
    finally:
        manager.close()


def test_migration_in_outer_transaction_fails_without_success_event_or_row(
    tmp_path: Path,
) -> None:
    """Runner не создаёт schema_migrations внутри чужой транзакции."""
    logger, stream = _logger()
    manager = DatabaseManager(_database_path(tmp_path))
    manager.connect()
    try:
        with pytest.raises(RuntimeError, match="rollback sentinel"):
            with manager.database.atomic():
                with pytest.raises(NestedStorageTransactionError, match="apply_migrations"):
                    apply_migrations(manager.database, logger=logger)
                assert "schema_migrations" not in manager.database.get_tables()
                raise RuntimeError("rollback sentinel")
        assert "schema_migrations" not in manager.database.get_tables()
        assert _records(stream) == []
        outcome = apply_migrations(manager.database, logger=logger)
        assert outcome.applied == (1,)
        assert _events(stream) == ["migration_started", "migration_completed"]
        assert current_schema_version(manager.database) == LATEST_SCHEMA_VERSION
    finally:
        manager.close()
