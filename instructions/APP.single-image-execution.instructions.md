---
applyTo: "src/aimedia/application/single_image.py,tests/integration/test_single_image.py"
name: "APP.SingleImageExecution"
description: "Читай при изменении одиночного image use case: D03 creation/validation/archive, один submit, подтверждение remote ref, ограниченное ожидание, billing до файлов и безопасная финализация истории."
---

# APP — Один image Job

Владелец — `application.single_image.generate_image`; источники —
[`08-job-execution.md`](../docs/plans/08-job-execution.md) и baseline D01/D02/D03.
Use case нового Job подключён к публичному CLI; batch/retry/sync и ownership —
[APP.image-execution](APP.image-execution.instructions.md). Общий
`finish_image_result` переиспользуется sync без submit.

- Caller читает/компилирует источники и получает `ReferenceSnapshot` **до** вызова.
  Первая запись — CREATED через `JobRepository`, с подтверждением полного Job и ID.
  Ошибка initial save останавливает поток; неизвестный ID не ищется по prompt.
- `prepare_provider` вызывается после CREATED внутри обязательной validation
  archive hook: наружная композиция разрешает модель/binding, проверяет параметры
  и создаёт gateway. Application не импортирует registry/providers/storage/artifacts.
  `ImageExecutionSetup` содержит только доменный binding и provider port.
  После подтверждённого CREATED validation hook вызывает `ArtifactStorage.preflight`
  до archive/submit: явный `--out` уже существует и доступен для записи, managed
  outputs готовится файловым adapter. Application не делает файловый IO этой проверки.
  Validation/setup/output-preflight/archive failure не делает submit; отказ записывается FAILED.
- Archive hook сохраняет все копии/links до платного запроса. В истории остаются
  provenance paths, в provider уходит только временный transport request. Файлы
  исходников после snapshot не перечитываются.
- Submit вызывается ровно один раз. Известные `remote_job_id`/`operation` и
  `submitted_at` подтверждаются до sleep/poll/fetch. При неизвестном paid outcome
  — `SUBMIT_UNCERTAIN`, `retryable=None`, без повтора и remote cancel.
- Интервал и deadline конечны и положительны. Clock/sleep инжектируются;
  одинаковые polling observations не создают status writes. Локальный timeout
  или interrupt сохраняет known ref; interrupt пробрасывается после записи ошибки.
  Recovery orchestration — в `application.execution`; общий finalizer допускает
  failed→completed только при `recovery=True`, сохраняя прежнюю ошибку.
  В sync общий finalizer с `raise_on_failure=True` сообщает текущую ошибку через
  `ImageAttemptFailed(job, error)` после подтверждения failure snapshot. Старый
  FAILED error остаётся в истории; presentation текущей попытки не выводится из него.
- Usage/actual Decimal cost подтверждаются **до** download/conversion. Unknown
  остаётся `None`. Billing находится в `Job.usage`/`Job.cost`, как хранит существующий
  repository; `JobResult` содержит локальные artifacts и безопасные metadata.
- `RemoteArtifact` locator, signed URL, base64 и provider text остаются только в
  памяти downloader callback. Для истории `redact` переиспользуется перед узким
  snapshot: raw usage сохраняет числовые/boolean/null метрики с machine keys;
  Decimal raw канонизируется JSON-строкой. Произвольные strings/containers
  отбрасываются; metadata сохраняет только числовые timestamps/warning_count.
  Resolved remote model ID хранится отдельным typed полем Job, не из raw metadata.
  Ошибка сохраняет проверенный код/retryable и фиксированное сообщение, не body/details.
- Output directory/base name/keep-original сохраняются как локальные execution
  metadata, не provider data. ORIGINAL имеет отдельную роль; partial FINAL positions
  переиспользуются recovery без повторной публикации.
- Все required image locators сохраняются; ранние файлы остаются partial result,
  последний проходит E05 `finalize_image_artifact`. COMPLETED возможен только после
  проверки опубликованного файла и подтверждения полного history snapshot.
- FS/SQLite не атомарны. После возможного commit сверяется только известный ID,
  без второго save/submit. Неподтверждённая история даёт `ImageHistoryError` с local
  snapshot ref/cost/partials; сообщение — только checked IDs/operation.
  После отказа финального DB commit история может оставаться SUBMITTED/RUNNING,
  файл сохраняется для сверки; это не доказанный rollback и не failed recovery.
  Опубликованные файлы никогда не удаляются. Finalizer переносит опубликованный
  Artifact и исходный KeyboardInterrupt/CancelledError через `ArtifactHistoryWriteError`;
  use case сверяет COMPLETED по известному ID и полному snapshot, затем пробрасывает
  тот же interrupt с безопасным `ImageHistoryError.job` в cause. Если commit не
  подтверждён, snapshot хранит ref/cost/файл, без повторной записи/cleanup.
  Перед любой failure write сверяется known ID: уже записанный COMPLETED нельзя
  заменить устаревшим non-terminal Job; несовпадение snapshot останавливает запись.
- Тесты используют disposable `tmp_path`, реальную SQLite, managed inputs и Pillow.
  Пользовательские данные не трогать; backup/restore/cleanup принадлежат
  [DATA.sqlite-history](DATA.sqlite-history.instructions.md) и
  [PROCESSING.image-artifacts](PROCESSING.image-artifacts.instructions.md).

Проверки: `uv run --locked --offline --no-env-file pytest tests/integration/test_single_image.py tests/integration/test_artifact_finalization.py tests/architecture/test_dependencies.py`,
затем offline `scripts/quality.py full`. Приёмка этапа требует независимого review
и gate committed SHA из чистого клона, а не только текущего dirty worktree.
