"""Peewee-реализация порта `JobRepository` (E04, C06b).

Repository — единственное место, где строка SQLite превращается в доменный
агрегат и обратно (`07-storage-history-costs.md`, «Repository layer»). Наружу не
выходит ни одна Peewee-запись: `save`/`get` возвращают `Job` с вложенными
доменными DTO.

Решения, отражённые в отображении:

- отдельные snapshot-колонки (`compiled_prompt`, `compiled_prompt_sha256`,
  `compiled_prompt_source_count`, `cost_amount`, `cost_currency`, `remote_job_id`,
  `operation`, `relation_type`) хранят значения в разложенном виде, поэтому
  история читается без разбора JSON;
- payload-колонки (`request_json`, `response_json`, `usage_json`, `error_json`,
  `recovery_json`, `metadata_json`) хранят тело соответствующего доменного DTO в
  provider-neutral форме; секретов, base64-байтов и бинарных данных там нет;
- `cost_amount` — decimal-as-TEXT: `Decimal` проходит через строку без
  промежуточного float, а `cost_currency` присутствует ровно тогда, когда известна
  сумма, поэтому «ноль в RUB» и «цена неизвестна» не сводятся друг к другу;
- timestamps пишутся в той же форме, что сериализация домена — ISO-8601 UTC с
  суффиксом `Z`, — и читаются обратно как timezone-aware UTC;
- `local_path` и `source_path` хранятся в posix-форме (`Path.as_posix`),
  одинаковой на Windows и Linux;
- порядок prompt sources, inputs и artifacts фиксируется колонкой `position`;
- артефакты хранятся одной таблицей, а их принадлежность коллекции `JobResult`
  записывается позициями в `response_json` (`_result_artifact_positions`). Без
  этого `save` → `get` не восстанавливал бы агрегат точно: домен допускает
  финальный artifact и в `Job.artifacts`, и в `Job.result.artifacts`;
- загрузка Job не обращается к Model Registry: восстанавливаются только
  сохранённые snapshot-поля, поэтому история не зависит от текущего Registry.

Сохранение атомарно: строка Job и её дочерние строки пишутся в одной транзакции,
поэтому частично записанный агрегат не наблюдается. Методы синхронны — транзакция
не удерживается во время сетевого вызова.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from peewee import OperationalError, SqliteDatabase

from aimedia.domain.artifacts import Artifact, ArtifactKind, ArtifactRole
from aimedia.domain.costs import Cost, Usage
from aimedia.domain.errors import JobError
from aimedia.domain.inputs import (
    CompiledPrompt,
    InputKind,
    InputRef,
    PromptSource,
    PromptSourceKind,
)
from aimedia.domain.job import Job, JobRecovery, JobRelation, JobResult
from aimedia.domain.refs import ModelRef, ProviderRef, RemoteJobRef, RemoteOperation
from aimedia.domain.requests import ImageGenerationRequest, JobKind
from aimedia.domain.state import JobStatus
from aimedia.logging import EventLogger
from aimedia.storage.database import DatabaseManager
from aimedia.storage.errors import (
    DatabaseBusyError,
    InvalidStoredJobStatusError,
    NestedStorageTransactionError,
    is_database_busy,
)
from aimedia.storage.events import (
    log_cost_recorded,
    log_job_created,
    log_job_state_changed,
    log_remote_ref_saved,
    log_usage_recorded,
)
from aimedia.storage.models import (
    ArtifactRecord,
    InputRecord,
    JobRecord,
    PromptSourceRecord,
)

# Ключ принадлежности артефакта коллекции `JobResult` внутри `response_json`.
# Начинается с подчёркивания, чтобы не пересечься с provider metadata: это
# структурная запись repository, а не часть нормализованного ответа provider.
RESULT_ARTIFACT_POSITIONS_KEY = "_result_artifact_positions"


@dataclass(frozen=True)
class _StoredJobState:
    """Ранее сохранённое состояние строки Job для сравнения с новым.

    Хранятся только те поля, по которым меняются события: `status`, пара
    `remote_job_id` + `operation` и сырые значения usage/cost. Сравнение идёт по
    тому же тексту, который repository записывает в колонки, поэтому «повторно
    сохранено без изменений» не дублирует событие, даже когда спаренный snapshot
    пришёл из строки БД, а не из домена.
    """

    status: JobStatus
    remote_job_id: str | None
    operation: str | None
    usage_json: str | None
    cost_amount: str | None
    cost_currency: str | None


def _dump_json(value: object) -> str:
    """Записать JSON стабильно и без экранирования не-ASCII.

    `sort_keys` нужен для воспроизводимого текста (сравнение и отладка истории),
    а `Path`/`Decimal`/`datetime`/enum внутри `value` уже приведены к строкам
    режимом `mode="json"` доменной сериализации.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _usage_json(usage: Usage) -> str:
    """Тот же текст, которым usage записывается в `usage_json`: сравнение и запись — одно."""
    return _dump_json(usage.model_dump(mode="json"))


def _cost_amount_text(cost: Cost) -> str:
    """Тот же текст, которым сумма записывается в `cost_amount` (TEXT, без float)."""
    return format(cost.amount, "f")


def _load_json(text: str | None) -> Any:
    """Разобрать payload-колонку; `NULL` остаётся `None`, а не пустым словарём."""
    if text is None or text == "":
        return None
    return json.loads(text)


def _utc_text(value: datetime) -> str:
    """Timestamp в форме сериализации домена: ISO-8601 UTC с суффиксом `Z`."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(text: str) -> datetime:
    """Прочитать timestamp истории как timezone-aware UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp истории без timezone: {text!r}")
    return parsed.astimezone(UTC)


def _path_text(value: Path | None) -> str | None:
    """Путь в posix-форме; `None` остаётся `NULL`, а не пустой строкой."""
    return None if value is None else value.as_posix()


def _optional_path(value: str | None) -> Path | None:
    """Прочитать путь; `NULL` означает отсутствие пути, а не `Path('.')`."""
    return None if value in (None, "") else Path(value)


class PeeweeJobRepository:
    """Синхронное хранение Job поверх Peewee (`JobRepository`).

    Repository не открывает соединений: менеджер проверяет владение и открытое
    состояние при каждом публичном вызове.
    Диагностика (`job_created`, `job_state_changed`, `remote_ref_saved`) включается
    необязательным `logger` в `save` (E04, C06c1). Ожидаемая блокировка SQLite
    (`busy_timeout` истёк) становится типизированной `DatabaseBusyError`: локальный
    отказ записи не смешивается с отказом provider.
    """

    def __init__(self, manager: DatabaseManager) -> None:
        if not isinstance(manager, DatabaseManager):
            raise TypeError("PeeweeJobRepository требует DatabaseManager")
        self._manager = manager

    @property
    def _database(self) -> SqliteDatabase:
        return self._manager.database

    def save(self, job: Job, *, logger: EventLogger | None = None) -> Job:
        """Создать или обновить Job и вернуть сохранённое состояние с `id`.

        Агрегат пишется целиком в одной транзакции: строка Job, дочерние строки
        (prompt sources, inputs, artifacts) и, при retry, ссылка на родительский
        Job. Повторный `save` того же Job с `id` обновляет его, а не создаёт
        вторую запись истории.

        Снимок сохранённого Job читается в той же транзакции после записи всех
        дочерних строк: следующий владелец SQLite не подменит его до события.
        События пишутся **после** commit и только по фактическому изменению:
        повторное сохранение неизменённого Job не повторяет `job_created`,
        `job_state_changed` или `remote_ref_saved`. Поэтому запись в поток не
        удерживает SQLite transaction и не удлиняет её.

        Истёкший `busy_timeout` — это `DatabaseBusyError` без частично записанной
        истории: транзакция откатывается целиком, а повтор остаётся решением
        вызывающего слоя (никакого скрытого retry и тем более повторного
        remote submit).

        Save внутри внешней транзакции запрещён: иначе событие могло бы быть
        записано до окончательного commit внешнего владельца транзакции.
        """
        if self._database.transaction_depth() > 0:
            raise NestedStorageTransactionError("save")
        try:
            with (
                # Reserve the writer slot before reading previous state. A DEFERRED
                # read followed by a concurrent commit can fail on upgrade with
                # SQLITE_BUSY_SNAPSHOT instead of honoring busy_timeout.
                self._database.atomic("IMMEDIATE"),
                self._database.bind_ctx(
                    [JobRecord, PromptSourceRecord, InputRecord, ArtifactRecord]
                ),
            ):
                previous = self._stored_state(job)
                record = self._upsert_job_row(job)
                self._replace_children(record, job)
                job_id = record.id
                saved = self.get(job_id)
                if saved is None:
                    raise ValueError(f"Job {job_id} не найден сразу после сохранения")
        except OperationalError as exc:
            if not is_database_busy(exc):
                raise
            raise DatabaseBusyError() from exc
        self._log_save(logger, previous=previous, saved=saved)
        return saved

    @staticmethod
    def _stored_state(job: Job) -> _StoredJobState | None:
        """Прочитать сохранённое состояние строки до её перезаписи.

        `None` означает новую строку истории: либо у Job ещё нет `id`, либо строки
        с таким `id` нет (тогда `_upsert_job_row` поднимет ошибку до событий).
        """
        if job.id is None:
            return None
        record = JobRecord.get_or_none(JobRecord.id == job.id)
        if record is None:
            return None
        # SQLite CharField не ограничивает значения enum; проверять до любой
        # перезаписи, иначе произвольный текст строки уйдёт в previous_status.
        if record.status not in {status.value for status in JobStatus}:
            raise InvalidStoredJobStatusError()
        return _StoredJobState(
            status=JobStatus(record.status),
            remote_job_id=record.remote_job_id,
            operation=record.operation,
            usage_json=record.usage_json,
            cost_amount=record.cost_amount,
            cost_currency=record.cost_currency,
        )

    def _log_save(
        self,
        logger: EventLogger | None,
        *,
        previous: _StoredJobState | None,
        saved: Job,
    ) -> None:
        """Записать события сохранения: ID, статусы и remote ref, без деталей Job."""
        if logger is None:
            return
        job_id = saved.id
        if job_id is None:  # pragma: no cover - save всегда возвращает сохранённый id
            return
        if previous is None:
            log_job_created(
                logger,
                job_id=job_id,
                kind=saved.kind.value,
                status=saved.status.value,
            )
        elif previous.status != saved.status:
            log_job_state_changed(
                logger,
                job_id=job_id,
                previous_status=previous.status.value,
                current_status=saved.status.value,
            )
        self._log_remote_ref(logger, previous=previous, saved=saved)
        self._log_usage(logger, previous=previous, saved=saved)
        self._log_cost(logger, previous=previous, saved=saved)

    @staticmethod
    def _log_remote_ref(
        logger: EventLogger | None,
        *,
        previous: _StoredJobState | None,
        saved: Job,
    ) -> None:
        """Записать `remote_ref_saved`, если ссылка появилась или изменилась."""
        job_id = saved.id
        remote_ref = saved.remote_ref
        if job_id is None or remote_ref is None:  # pragma: no cover - см. `_log_save`
            return
        operation = remote_ref.operation
        unchanged = previous is not None and (
            previous.remote_job_id == remote_ref.remote_job_id
            and previous.operation == (None if operation is None else operation.value)
        )
        if unchanged:
            return
        log_remote_ref_saved(
            logger,
            job_id=job_id,
            operation=operation,
        )

    @staticmethod
    def _log_usage(
        logger: EventLogger | None,
        *,
        previous: _StoredJobState | None,
        saved: Job,
    ) -> None:
        """Записать `usage_recorded` при появлении или изменении usage.

        Сравнение идёт с той же строкой, что записана в `usage_json`, поэтому
        повторный save неизменённого Job (в том числе после `get`, когда доменное
        значение уже прошло round trip) не дублирует событие.
        """
        job_id = saved.id
        usage = saved.usage
        if job_id is None or usage is None:  # pragma: no cover - см. `_log_save`
            return
        if previous is not None and previous.usage_json == _usage_json(usage):
            return
        log_usage_recorded(logger, job_id=job_id, usage=usage)

    @staticmethod
    def _log_cost(
        logger: EventLogger | None,
        *,
        previous: _StoredJobState | None,
        saved: Job,
    ) -> None:
        """Записать `cost_recorded` при появлении или изменении стоимости.

        Известный ноль (`0`) отличается от неизвестной цены (`cost is None`):
        последняя события не пишет, а не сообщает нулевой расход.
        """
        job_id = saved.id
        cost = saved.cost
        if job_id is None or cost is None:  # pragma: no cover - см. `_log_save`
            return
        if previous is not None and (
            previous.cost_amount == _cost_amount_text(cost)
            and previous.cost_currency == cost.currency
        ):
            return
        log_cost_recorded(logger, job_id=job_id, amount=cost.amount, currency=cost.currency)

    def get(self, job_id: int) -> Job | None:
        """Прочитать Job по локальному ID; `None`, если такого Job нет."""
        with self._database.bind_ctx([JobRecord, PromptSourceRecord, InputRecord, ArtifactRecord]):
            record = JobRecord.get_or_none(JobRecord.id == job_id)
            if record is None:
                return None
            return self._to_domain(record)

    def list_recent(
        self,
        *,
        limit: int = 20,
        statuses: Sequence[JobStatus] | None = None,
    ) -> Sequence[Job]:
        """Вернуть последние Job, при необходимости только с указанными статусами.

        Сортировка по убыванию локального ID: ID монотонен и не зависит от
        точности timestamp, поэтому порядок стабилен даже у Job, созданных в одну
        секунду.
        """
        with self._database.bind_ctx([JobRecord, PromptSourceRecord, InputRecord, ArtifactRecord]):
            query = JobRecord.select()
            if statuses is not None:
                query = query.where(JobRecord.status.in_([status.value for status in statuses]))
            ordered = query.order_by(JobRecord.id.desc()).limit(limit)
            return [self._to_domain(record) for record in ordered]

    # --- Запись агрегата -----------------------------------------------------

    def _upsert_job_row(self, job: Job) -> JobRecord:
        """Записать строку Job (snapshot-поля) и вернуть её с присвоенным ID."""
        prompt = job.compiled_prompt
        cost = job.cost
        _, result_positions = self._ordered_artifacts(job)

        fields: dict[str, object] = {
            "kind": job.kind.value,
            "status": job.status.value,
            "provider_id": job.provider.id,
            "model_id": job.model.id,
            "remote_model_id": job.remote_model_id,
            "remote_job_id": job.remote_ref.remote_job_id if job.remote_ref else None,
            "operation": (
                job.remote_ref.operation.value
                if job.remote_ref is not None and job.remote_ref.operation is not None
                else None
            ),
            "compiled_prompt": prompt.text if prompt is not None else None,
            "compiled_prompt_sha256": prompt.sha256 if prompt is not None else None,
            "compiled_prompt_source_count": prompt.source_count if prompt is not None else None,
            "request_json": _dump_json(job.request.model_dump(mode="json")),
            "response_json": self._response_snapshot(job.result, result_positions),
            "usage_json": _usage_json(job.usage) if job.usage is not None else None,
            "cost_amount": _cost_amount_text(cost) if cost is not None else None,
            "cost_currency": cost.currency if cost is not None else None,
            "error_code": job.error.code if job.error is not None else None,
            "error_message": job.error.message if job.error is not None else None,
            "error_json": self._error_detail(job.error),
            "parent_job_id": job.relation.parent_job_id if job.relation is not None else None,
            "relation_type": job.relation.type if job.relation is not None else None,
            "recovery_json": (
                _dump_json(job.recovery.model_dump(mode="json"))
                if job.recovery is not None
                else None
            ),
            "created_at": _utc_text(job.created_at),
            "submitted_at": _utc_text(job.submitted_at) if job.submitted_at else None,
            "started_at": _utc_text(job.started_at) if job.started_at else None,
            "completed_at": _utc_text(job.completed_at) if job.completed_at else None,
        }

        if job.id is None:
            return cast(JobRecord, JobRecord.create(**fields))
        JobRecord.update(**fields).where(JobRecord.id == job.id).execute()
        record = JobRecord.get_or_none(JobRecord.id == job.id)
        if record is None:
            raise ValueError(f"Job с id={job.id} не найден для обновления")
        return cast(JobRecord, record)

    @staticmethod
    def _response_snapshot(result: JobResult | None, result_positions: Sequence[int]) -> str | None:
        """Ответ provider: текстовая часть результата и принадлежность артефактов.

        Локальные артефакты здесь не дублируются: их байты и метаданные живут в
        таблице `artifacts`, а payload фиксирует, какие позиции этой таблицы
        относятся к `Job.result.artifacts`. Секреты, base64 и бинарные данные в
        snapshot не попадают.
        """
        if result is None:
            return None
        return _dump_json(
            {
                "content": result.content,
                "metadata": dict(result.metadata),
                RESULT_ARTIFACT_POSITIONS_KEY: list(result_positions),
            }
        )

    @staticmethod
    def _error_detail(error: JobError | None) -> str | None:
        """Детали ошибки без дублирования нормализованного кода и сообщения."""
        if error is None:
            return None
        return _dump_json(
            {
                "provider_code": error.provider_code,
                "provider_message": error.provider_message,
                "retryable": error.retryable,
                "details": dict(error.details),
            }
        )

    @staticmethod
    def _ordered_artifacts(job: Job) -> tuple[tuple[Artifact, ...], tuple[int, ...]]:
        """Порядок строк artifacts и позиции артефактов, входящих в `Job.result`.

        Артефакты двух допустимых коллекций (`Job.artifacts` и
        `Job.result.artifacts`) хранятся одной таблицей: сначала агрегатный список,
        затем содержимое result. Каждая запись коллекции сохраняется ровно один раз,
        поэтому `save` → `get` восстанавливает агрегат без потери состава и порядка.
        """
        result_artifacts = job.result.artifacts if job.result else ()
        ordered = (*job.artifacts, *result_artifacts)
        result_positions = tuple(range(len(job.artifacts), len(ordered)))
        return ordered, result_positions

    def _replace_children(self, record: JobRecord, job: Job) -> None:
        """Переписать дочерние строки агрегата в исходном порядке.

        Удаление и вставка идут в той же транзакции, что и строка Job, поэтому
        сохранённый агрегат не наблюдается частично. Внешние ключи объявлены с
        `CASCADE`, но дочерние строки удаляются явно: результат не зависит от
        момента включения `PRAGMA foreign_keys`.
        """
        PromptSourceRecord.delete().where(PromptSourceRecord.job == record).execute()
        InputRecord.delete().where(InputRecord.job == record).execute()
        ArtifactRecord.delete().where(ArtifactRecord.job == record).execute()

        for source in job.prompt_sources:
            PromptSourceRecord.create(
                job=record.id,
                kind=source.kind.value,
                position=source.position,
                source_path=_path_text(source.path),
                text_snapshot=source.text,
                sha256=None,
            )

        for input_ref in job.inputs:
            InputRecord.create(
                job=record.id,
                kind=input_ref.kind.value,
                position=input_ref.position,
                source_path=input_ref.path.as_posix(),
                mime_type=input_ref.mime_type,
                size_bytes=input_ref.size_bytes,
                sha256=input_ref.sha256,
                metadata_json=_dump_json(dict(input_ref.metadata)),
            )

        ordered_artifacts, _ = self._ordered_artifacts(job)
        for position, artifact in enumerate(ordered_artifacts):
            ArtifactRecord.create(
                job=record.id,
                kind=artifact.kind.value,
                role=artifact.role.value,
                position=position,
                local_path=_path_text(artifact.local_path),
                remote_url=artifact.remote_url,
                mime_type=artifact.mime_type,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                metadata_json=_dump_json(dict(artifact.metadata)),
                created_at=_utc_text(job.completed_at or job.created_at),
            )

    # --- Чтение агрегата -----------------------------------------------------

    def _to_domain(self, record: JobRecord) -> Job:
        """Собрать доменный `Job` из строки и её дочерних строк."""
        artifact_rows = self._artifact_rows(record)
        result = self._result(record, artifact_rows)
        result_positions = set(self._result_positions(record))
        artifacts = tuple(
            self._artifact(row)
            for position, row in enumerate(artifact_rows)
            if position not in result_positions
        )

        return Job(
            id=record.id,
            kind=JobKind(record.kind),
            status=JobStatus(record.status),
            provider=ProviderRef(id=record.provider_id),
            model=ModelRef(id=record.model_id),
            remote_model_id=record.remote_model_id,
            request=self._request(record),
            inputs=self._inputs(record),
            prompt_sources=self._prompt_sources(record),
            compiled_prompt=self._compiled_prompt(record),
            result=result,
            artifacts=artifacts,
            usage=self._usage(record),
            cost=self._cost(record),
            remote_ref=self._remote_ref(record),
            error=self._error(record),
            relation=self._relation(record),
            recovery=self._recovery(record),
            created_at=_parse_utc(record.created_at),
            submitted_at=self._timestamp(record.submitted_at),
            started_at=self._timestamp(record.started_at),
            completed_at=self._timestamp(record.completed_at),
        )

    @staticmethod
    def _timestamp(text: str | None) -> datetime | None:
        return None if text in (None, "") else _parse_utc(text)

    @staticmethod
    def _result_payload(record: JobRecord) -> dict[str, Any] | None:
        payload = _load_json(record.response_json)
        return None if payload is None else dict(payload)

    @classmethod
    def _result_positions(cls, record: JobRecord) -> tuple[int, ...]:
        """Позиции строк artifacts, принадлежащих `Job.result.artifacts`."""
        payload = cls._result_payload(record)
        if payload is None:
            return ()
        return tuple(int(value) for value in payload.get(RESULT_ARTIFACT_POSITIONS_KEY) or ())

    def _request(self, record: JobRecord) -> ImageGenerationRequest:
        payload = _load_json(record.request_json)
        if payload is None:
            raise ValueError(f"Job {record.id} без сохранённого request snapshot")
        return ImageGenerationRequest.model_validate(payload)

    @staticmethod
    def _compiled_prompt(record: JobRecord) -> CompiledPrompt | None:
        """Собрать compiled prompt из разложенных snapshot-колонок."""
        if record.compiled_prompt is None:
            return None
        if record.compiled_prompt_source_count is None:
            raise ValueError(f"Job {record.id} без числа источников compiled prompt")
        return CompiledPrompt(
            text=record.compiled_prompt,
            source_count=record.compiled_prompt_source_count,
            sha256=record.compiled_prompt_sha256,
        )

    @staticmethod
    def _prompt_sources(record: JobRecord) -> list[PromptSource]:
        return [
            PromptSource(
                kind=PromptSourceKind(row.kind),
                text=row.text_snapshot,
                position=row.position,
                path=_optional_path(row.source_path),
            )
            for row in PromptSourceRecord.select()
            .where(PromptSourceRecord.job == record.id)
            .order_by(PromptSourceRecord.position)
        ]

    @staticmethod
    def _inputs(record: JobRecord) -> list[InputRef]:
        return [
            InputRef(
                kind=InputKind(row.kind),
                path=Path(row.source_path),
                position=row.position,
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                sha256=row.sha256,
                metadata=dict(_load_json(row.metadata_json) or {}),
            )
            for row in InputRecord.select()
            .where(InputRecord.job == record.id)
            .order_by(InputRecord.position)
        ]

    @staticmethod
    def _artifact_rows(record: JobRecord) -> list[ArtifactRecord]:
        return list(
            ArtifactRecord.select()
            .where(ArtifactRecord.job == record.id)
            .order_by(ArtifactRecord.position)
        )

    def _result(
        self, record: JobRecord, artifact_rows: Sequence[ArtifactRecord]
    ) -> JobResult | None:
        """Восстановить нормализованный результат и его артефакты.

        Артефакты, перечисленные в `response_json`, восстанавливаются в
        сохранённом порядке. Для `completed` image Job домен сам потребует
        сохранённый финальный artifact и поднимет ошибку, если история
        противоречива: repository не подменяет инвариант частичными данными.
        """
        payload = self._result_payload(record)
        if payload is None:
            return None
        positions = self._result_positions(record)
        artifacts = tuple(
            self._artifact(artifact_rows[position])
            for position in positions
            if 0 <= position < len(artifact_rows)
        )
        return JobResult(
            artifacts=artifacts,
            content=payload.get("content"),
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _artifact(row: ArtifactRecord) -> Artifact:
        return Artifact(
            kind=ArtifactKind(row.kind),
            role=ArtifactRole(row.role),
            local_path=_optional_path(row.local_path),
            remote_url=row.remote_url,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            metadata=dict(_load_json(row.metadata_json) or {}),
        )

    @staticmethod
    def _usage(record: JobRecord) -> Usage | None:
        payload = _load_json(record.usage_json)
        return None if payload is None else Usage.model_validate(payload)

    @staticmethod
    def _cost(record: JobRecord) -> Cost | None:
        """Деньги читаются из decimal-as-TEXT пары `cost_amount` + `cost_currency`."""
        if record.cost_amount is None:
            if record.cost_currency is not None:
                raise ValueError(f"Job {record.id}: валюта без суммы")
            return None
        if record.cost_currency is None:
            raise ValueError(f"Job {record.id}: сумма без валюты")
        return Cost(amount=Decimal(record.cost_amount), currency=record.cost_currency)

    @staticmethod
    def _error(record: JobRecord) -> JobError | None:
        if record.error_code is None:
            return None
        detail = _load_json(record.error_json) or {}
        return JobError(
            code=record.error_code,
            message=record.error_message or "",
            provider_code=detail.get("provider_code"),
            provider_message=detail.get("provider_message"),
            retryable=detail.get("retryable"),
            details=dict(detail.get("details") or {}),
        )

    @staticmethod
    def _recovery(record: JobRecord) -> JobRecovery | None:
        payload = _load_json(record.recovery_json)
        return None if payload is None else JobRecovery.model_validate(payload)

    @staticmethod
    def _remote_ref(record: JobRecord) -> RemoteJobRef | None:
        """Remote ID и тип операции восстанавливаются вместе, без угадывания endpoint."""
        if record.remote_job_id is None:
            return None
        return RemoteJobRef(
            provider_id=record.provider_id,
            remote_job_id=record.remote_job_id,
            operation=(None if record.operation is None else RemoteOperation(record.operation)),
        )

    @staticmethod
    def _relation(record: JobRecord) -> JobRelation | None:
        if record.parent_job_id is None:
            return None
        return JobRelation(
            parent_job_id=cast(int, record.parent_job_id.id),
            type=record.relation_type or "retry_of",
        )
