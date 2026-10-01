---
applyTo: "src/aimedia/application/execution.py,src/aimedia/bootstrap.py,src/aimedia/storage/ownership.py,tests/integration/test_execution_ownership.py,tests/cli/test_complete_cli.py"
name: "APP.ImageExecution"
description: "Читай при изменении batch/retry/sync, composition root и локального Job ownership: semaphore полного flow, durable copies, recovery того же ref без POST, kernel locks Windows/POSIX и crash release."
---

# APP — Связное выполнение image Jobs

Владельцы: `application.execution` — orchestration над domain ports;
`bootstrap.LocalApplication` — единственная production-композиция SQLite/Registry/
Polza/FS. Источники — [08](../docs/plans/08-job-execution.md), baseline D01–D03/D14.
Одиночный execution/finalization — [APP.single-image-execution](APP.single-image-execution.instructions.md).

- Источники читаются до первой записи CREATED. `create_image_job` подтверждает
  весь snapshot. Batch создаёт все записи до запуска tasks; semaphore удерживается
  от ownership/pre-submit до конечной финализации. Expected FAILED Job возвращается
  как результат и не отменяет соседей. При локальной отмене ожидающие CREATED
  переходят в CANCELLED; known remote refs не удаляются, remote cancel нет.
- `execute_image` берёт Job lock и перечитывает CREATED до единственного submit.
  `storage.ownership.claim_job` использует Windows `msvcrt` byte lock / POSIX `flock`
  на постоянном `locks/<id>.lock`. Kernel ownership снимается ОС при crash; никаких
  PID/time-based stale guesses. Lock-файл не удаляется: unlink создаёт гонку inode.
  До mkdir/open проверяются redirects, ID положительный. Это local guard, не broker.
- Retry явно создаёт новый Job/retry_of и читает проверенные managed input bytes
  без fallback к исходнику; старый aggregate/cost/result не меняется. Нет overrides
  или автоматического paid retry.
- Sync только get_status/fetch_result/download; submit никогда не вызывается.
  COMPLETED — local no-op. Unknown submit без ref не обещает recovery. FAILED
  остаётся FAILED при running observation; failed→completed разрешён только
  recovery того же ref с `recovery.previous_error`. Сохранённые partial positions
  переиспользуются без нового файла; стоимость — snapshot, не прибавление.
  `PROVIDER_INCOMPLETE_RESULT` пригодного normalized результата — recoverable
  same-ref failure; повторный count GET не требует нового POST. Zero-image
  malformed response остаётся отдельным invalid-response, без нового billing.
  `SyncResult.error` описывает только текущую попытку. Новый GET/remote/local отказ
  нельзя выдавать за success или классифицировать по старому `Job.error`.
  Успешное running observation старого FAILED остаётся success; общий finalizer
  передаёт текущую ошибку через `ImageAttemptFailed`, не стирая старую историю.
  Непосредственный GET отказ использует тот же `_safe_error`, что single image:
  strict numeric HTTP status и finite provider code/reason по
  [APP.single-image-execution](APP.single-image-execution.instructions.md),
  без provider text/trace/raw. Diagnostics текущей попытки не переносятся вместо
  прежней ошибки FAILED Job; новый FAILED сохраняет безопасные поля.
- GET retry — не больше трёх попыток, только retryable ошибки; finite HTTP timeout
  и общий deadline обычного ожидания. POST retry отсутствует технически.
- Usage/cost подтверждаются до локального download/conversion, отсутствующий
  новый cost не стирает ранее известный. Signed URL и произвольный provider text
  остаются в памяти. KeyboardInterrupt/CancelledError после возможного commit
  сохраняют оригинальное исключение и опубликованный forensic snapshot.

Источник истины — SQLite плюс managed bytes. Перед ручной рискованной операцией
над существующим data-root нужен проверенный quiescent backup DB/inputs/outputs,
manifest/hash и restore по [DATA](DATA.sqlite-history.instructions.md).
Тесты — disposable tmp roots; ни миграций пользовательских данных, ни cleanup.
Offline assertions — `tests/cli/test_complete_cli.py`,
`tests/cli/test_subprocess_generation.py` (включая batch SIGINT: queued CREATED →
CANCELLED, active ref/cost/copies после restart, освобождение ownership без cancel),
`tests/integration/test_execution_ownership.py`.
Приёмка — единый offline quality full/release и независимый review frozen SHA;
локальная реализация не доказывает live или Linux-приёмку.
