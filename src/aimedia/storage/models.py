"""Peewee-модели локальной истории aimedia (`07-storage-history-costs.md`).

Это схема хранения, а не доменные объекты: repository (C06b/C07) преобразует
`Peewee record ↔ domain object`. Здесь зафиксированы только таблицы и ограничения,
которые обязаны переживать перезапуск процесса.

Решения, отражённые в схеме:

- деньги хранятся decimal-as-TEXT (`cost_amount`), а валюта — отдельной колонкой
  `cost_currency`; `cost_amount` и `cost_currency` присутствуют вместе или
  отсутствуют вместе (baseline D11, «ноль ≠ unknown»);
- порядок prompt sources, inputs и artifacts фиксируется колонкой `position` с
  уникальностью в пределах Job;
- `remote_job_id` и `operation` хранятся в snapshot Job рядом, endpoint по строке
  ID не угадывается (baseline D10);
- timestamps хранятся строкой ISO-8601 UTC с суффиксом `Z` — та же форма, что у
  сериализации домена;
- внешние ключи объявлены в схеме; фактическое включение `PRAGMA foreign_keys`
  выполняет `DatabaseManager`.

Модели привязаны к `DatabaseProxy`, поэтому конкретная база подставляется на время
операции (`bind_ctx`), а не глобально на импорте.
"""

from __future__ import annotations

from peewee import (
    AutoField,
    CharField,
    Check,
    DatabaseProxy,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

# Прокси, на который замыкаются модели. Реальная база подставляется через
# `Database.bind_ctx(...)`, поэтому импорт модуля не открывает ни одного соединения.
database_proxy = DatabaseProxy()

# Общая таблица учёта применённых миграций: `version` + `applied_at`
# (`07-storage-history-costs.md`, таблица `schema_migrations`).
MIGRATION_TABLE_NAME = "schema_migrations"


# Peewee не типизирован (см. override в pyproject): базовый класс приходит как
# `Any`, поэтому подкласс помечен явно, а не спрятан от mypy молча.
class StorageModel(Model):  # type: ignore[misc]
    """Базовая модель storage: привязка к прокси, явные имена таблиц."""

    class Meta:
        database = database_proxy
        legacy_table_names = False


class JobRecord(StorageModel):
    """Одна строка истории — один локальный Job.

    Snapshot-поля (provider, model, request, response, usage, cost, error)
    сохраняются в самой строке, чтобы история не зависела от будущего изменения
    Registry или provider config.
    """

    id = AutoField()

    kind = CharField(null=False)
    status = CharField(null=False)

    provider_id = CharField(null=False)
    model_id = CharField(null=False)
    remote_model_id = CharField(null=True)

    remote_job_id = CharField(null=True)
    operation = CharField(null=True)

    compiled_prompt = TextField(null=True)
    compiled_prompt_sha256 = CharField(null=True)
    compiled_prompt_source_count = IntegerField(null=True)

    request_json = TextField(null=True)
    response_json = TextField(null=True)
    usage_json = TextField(null=True)

    cost_amount = TextField(null=True)
    cost_currency = CharField(null=True)

    error_code = CharField(null=True)
    error_message = TextField(null=True)
    error_json = TextField(null=True)

    parent_job_id = ForeignKeyField(
        "self", backref="derived_jobs", field="id", null=True, on_delete="RESTRICT"
    )
    relation_type = CharField(null=True)
    recovery_json = TextField(null=True)

    created_at = CharField(null=False)
    submitted_at = CharField(null=True)
    started_at = CharField(null=True)
    completed_at = CharField(null=True)

    class Meta:
        table_name = "jobs"
        constraints = [
            # Валюта обязательна, если сумма известна, и наоборот: «ноль в RUB» и
            # «цена неизвестна» не сводятся друг к другу.
            Check("(cost_amount IS NULL) = (cost_currency IS NULL)"),
        ]
        indexes = (
            (("status",), False),
            (("created_at",), False),
        )


class PromptSourceRecord(StorageModel):
    """Один источник prompt в исходном порядке CLI."""

    id = AutoField()
    job = ForeignKeyField(JobRecord, backref="prompt_sources", on_delete="CASCADE")

    kind = CharField(null=False)
    position = IntegerField(null=False)

    source_path = CharField(null=True)
    text_snapshot = TextField(null=False)
    sha256 = CharField(null=True)

    class Meta:
        table_name = "prompt_sources"
        indexes = ((("job", "position"), True),)


class InputRecord(StorageModel):
    """Внешний входной ресурс Job (в v0.1 — reference image)."""

    id = AutoField()
    job = ForeignKeyField(JobRecord, backref="inputs", on_delete="CASCADE")

    kind = CharField(null=False)
    position = IntegerField(null=False)

    source_path = CharField(null=False)
    mime_type = CharField(null=True)
    size_bytes = IntegerField(null=True)
    sha256 = CharField(null=True)
    metadata_json = TextField(null=True)

    class Meta:
        table_name = "inputs"
        indexes = ((("job", "position"), True),)


class ArtifactRecord(StorageModel):
    """Сохранённый локальный артефакт Job.

    `local_path` — главная ссылка на реальный файл; `remote_url` — только
    provenance provider и не считается постоянным результатом.
    """

    id = AutoField()
    job = ForeignKeyField(JobRecord, backref="artifacts", on_delete="CASCADE")

    kind = CharField(null=False)
    role = CharField(null=False)
    position = IntegerField(null=False)

    local_path = CharField(null=True)
    remote_url = TextField(null=True)
    mime_type = CharField(null=True)
    size_bytes = IntegerField(null=True)
    sha256 = CharField(null=True)
    metadata_json = TextField(null=True)

    created_at = CharField(null=False)

    class Meta:
        table_name = "artifacts"
        indexes = ((("job", "position"), True),)


class SchemaMigrationRecord(StorageModel):
    """Применённая миграция схемы.

    Версия — первичный ключ: повторная запись той же версии невозможна, поэтому
    «успех» миграции не может быть записан дважды.
    """

    version = IntegerField(primary_key=True)
    name = CharField(null=False)
    applied_at = CharField(null=False)

    class Meta:
        table_name = MIGRATION_TABLE_NAME
        legacy_table_names = False


# Все таблицы данных первой версии схемы. `schema_migrations` создаётся самим
# runner'ом и в этот список не входит.
SCHEMA_TABLES: tuple[type[StorageModel], ...] = (
    JobRecord,
    PromptSourceRecord,
    InputRecord,
    ArtifactRecord,
)

# Модели, которым нужна привязка к конкретной базе на время операции.
ALL_MODELS: tuple[type[StorageModel], ...] = (*SCHEMA_TABLES, SchemaMigrationRecord)
