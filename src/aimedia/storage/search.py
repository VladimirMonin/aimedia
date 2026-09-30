"""Small lexical FTS5 history view; Job snapshots remain the source of truth."""

from __future__ import annotations

from collections.abc import Sequence

from peewee import DatabaseError, OperationalError

from aimedia.domain.errors import InvalidParameterValueError
from aimedia.domain.job import Job
from aimedia.domain.state import JobStatus
from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import StorageError
from aimedia.storage.repository import PeeweeJobRepository


class HistorySearch:
    def __init__(self, manager: DatabaseManager, repository: PeeweeJobRepository) -> None:
        self._manager = manager
        self._repository = repository

    def search(
        self, query: str, *, limit: int = 20, status: JobStatus | None = None
    ) -> Sequence[Job]:
        if not query.strip() or not 1 <= limit <= 1000:
            raise InvalidParameterValueError("Нужны непустой запрос и limit 1–1000")
        # Literal lexical terms, not user SQL or an unbounded FTS query language.
        expression = " AND ".join('"' + word.replace('"', '""') + '"' for word in query.split())
        sql = "SELECT j.id FROM jobs_fts JOIN jobs j ON j.id=jobs_fts.rowid WHERE jobs_fts MATCH ?"
        values: list[object] = [expression]
        if status is not None:
            sql += " AND j.status=?"
            values.append(status.value)
        sql += " ORDER BY jobs_fts.rank, j.id DESC LIMIT ?"
        values.append(limit)
        try:
            rows = self._manager.database.execute_sql(sql, values).fetchall()
            return tuple(
                job for row in rows if (job := self._repository.get(int(row[0]))) is not None
            )
        except DatabaseError as exc:
            if type(exc) not in (DatabaseError, OperationalError):
                raise  # Unexpected ORM/programming errors are not storage IO failures.
            raise StorageError("Не удалось прочитать индекс истории") from exc
