---
applyTo: "src/aimedia/storage/**,tests/integration/test_migrations.py,tests/integration/test_migration_failure.py,tests/integration/test_job_repository.py,tests/integration/test_storage_events.py,tests/integration/test_sqlite_contention.py,tests/integration/test_managed_inputs.py,tests/integration/test_manual_managed_backup.py"
name: "DATA.SqliteHistory"
description: "Читай при изменении SQLite-истории aimedia: schema/migration runner, модели и repositories в src/aimedia/storage/ или их offline-тестов — версии v1/v2, неизменность v1 DDL, короткие транзакции, связь Job → managed-копия, disposable tmp-базы и проверенный quiescent backup перед рискованной миграцией/cleanup или ручной операцией агента с существующим data-root."
---

# DATA — SQLite-история aimedia

Владелец — `src/aimedia/storage/` (схема `models.py`, единственный runner
`migrations.py`, владелец соединения `database.py`, `repository.py`); план-источник —
[`07-storage-history-costs.md`](../docs/plans/07-storage-history-costs.md).

## Версии схемы

- **v1** (`001_initial`): `jobs`, `prompt_sources`, `inputs`, `artifacts`
  (`SCHEMA_TABLES`). Её DDL **не переписывается**: новая таблица входит следующим
  шагом `MIGRATIONS`, а не правкой v1 и не новой колонкой в `inputs`. Если задача
  «требует поправить v1» — это решение владельца, а не тихая правка.
- **v2** (`002_managed_input_copies`, `CN-01`): таблица `managed_input_copies`,
  `input_id` — PK и FK на `inputs(id)` с `ON DELETE CASCADE`, `local_path` — NOT NULL
  и UNIQUE. Строка есть только у входа с managed-копией; её отсутствие — legacy-вход,
  а не повреждение. Строка допустима только при заполненных `sha256`, MIME и размере,
  а прочитанное значение идёт в домен **сырой строкой**: пустой путь, `//` или `./` —
  отказ чтения (fail-closed), а не legacy-`None`. Hash, MIME, размер, позиция и
  `source_path` остаются в `inputs`.
- Уже записанные строки **не backfill-ятся** и не переклассифицируются: миграция не
  читает пользовательские файлы и не меняет completed Jobs.
- Каждая версия — в своей транзакции; повторный `apply_migrations` — no-op; схема «из
  будущего» не понижается (`SchemaTooNewError`), разрыв версий — `MigrationDefinitionError`.

## Bindings, repository, транзакции

- `ALL_MODELS` — runtime bindings (таблицы v1, v2, `schema_migrations`). Новая
  связанная таблица обязана попасть в `ALL_MODELS` и в `_JOB_MODELS` repository,
  иначе чтение упадёт после reopen.
- `save` Job пересоздаёт дочерние строки в одной транзакции, поэтому строка
  `managed_input_copies` создаётся заново с новым `input.id`; файловое имя копии
  зависит от Job ID и position, а не от `input.id`. Нарушение `UNIQUE(local_path)`
  откатывает весь `save`, не теряя прежний Job и его связи.
- Копия входа — не `Artifact`: она не входит в `JobResult` и не влияет на `--out`.
- Транзакция storage короткая: без `await`, сети и пользовательских подтверждений
  внутри. Ожидаемая блокировка — `DatabaseBusyError`, без скрытого retry.

## Тестовые данные и backup пользовательских данных

- Тесты storage работают в `tmp_path` и offline; пользовательский data-root не
  создаётся и не изменяется.
- До рискованной миграции, cleanup или **ручной операции агента** с существующим
  пользовательским data-root нужен **проверенный** quiescent offline backup:
  writers/БД закрыты, WAL снят согласованно, манифест path/size/SHA-256 совпадает
  с байтами, restore проверен в изолированном каталоге
  ([`backup-contract.md`](../docs/plans/backup-contract.md)). Обычная запись Job
  (создание/обновление истории) такого backup не требует. Backup CLI в v0.1 нет,
  и runtime-блокировка миграций под него не добавляется. Непроверенная копия —
  причина остановить опасную операцию, а не продолжить с ней.

## Что не реализовано

Схема v2 и repository связи сохраняются без изменений файловым срезом CN-01.
Application hook подтверждает полный Job (ID, provenance/managed paths, SHA/MIME/size,
все позиции) после save и get. При save exception после возможного commit — сверка
только известного Job, без повторной записи; без совпадения submit запрещён,
опубликованные копии не удаляются. FS не входит в SQLite-транзакцию.

Файловый adapter и ручной backup/restore offline-тест реализованы (owner —
[PROCESSING.image-artifacts](PROCESSING.image-artifacts.instructions.md));
полная E07/CLI/history-композиция, runtime backup-манифест, FTS5 и maintenance-команды
не реализованы. Legacy-записи не получают копий задним числом.
