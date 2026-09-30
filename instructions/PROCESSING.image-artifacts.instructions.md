---
applyTo: "src/aimedia/artifacts/**,src/aimedia/application/artifact_finalization.py,src/aimedia/application/inputs/archive.py,tests/integration/test_artifact_*.py,tests/integration/test_managed_inputs.py,tests/integration/test_manual_managed_backup.py,tests/integration/test_output_collisions.py,tests/security/test_output_names.py"
name: "PROCESSING.ImageArtifacts"
description: "Читай при изменении image artifact storage/finalization и managed-копий входов: декодирование PNG/JPEG/WebP, managed и --out пути, no-clobber публикация, ошибки, согласованность файлов с Job history и инвариант managed-копий reference images (CN-01: schema v2 и файловый шов; без полного E07/CLI)."
---

# PROCESSING — Локальные image artifacts

Источник истины — подтверждённый снимок Job в SQLite **вместе с** байтами
managed-файлов. Порт `ArtifactStorage` возвращает Artifact только после полного
декодирования, разрешённой конвертации и no-clobber публикации; первое изменение
файловой системы в managed-ветке — создание каталога `outputs/<job_id>`, затем
временного файла. До создания каталога проверь положительный ID, допустимое
состояние Job, размер/целостность изображения и существующие перенаправления;
имя проверяется до создания временного файла. `--out` выбирает уже существующий
пользовательский каталог: не создавай его, не делай скрытую managed-копию и не
удаляй общей очисткой.

- Для PNG/JPEG/WebP проверяй реальные входные **и конечные** байты, пиксельные
  лимиты и размеры. PNG/WebP сохраняют alpha (включая PNG `tRNS` RGB/L/P);
  JPEG накладывает её на белый фон и отмечает `alpha_flattened`. `ORIGINAL`
  сохраняет проверенный исходный контейнер отдельно от `FINAL`; повторный
  provider submit при локальном отказе запрещён.
- Относительный managed `local_path` имеет форму `outputs/<job_id>/<file>`;
  внешний пользовательский путь абсолютен. Отклоняй `..`, выход за root и
  symlink/junction в существующих компонентах. Одновременная враждебная подмена
  каталога после preflight вне объявленной Windows stdlib threat model.
- Hash/size/MIME отражают опубликованные конечные байты; не копируй MIME от
  provider и не обозначай `completed` до подтверждения записи Job. После
  публикации и сбоя истории файл **не** удаляй: по Artifact можно сверить
  фактический путь/hash с историей. Исключение после возможного DB commit не
  доказывает rollback: сначала сверить запись по локальному ID; без сверки не
  повторять submit и не обещать orphan как установленный факт.
- Диагностика artifacts не содержит URL/query, prompt, локальных путей, basename
  или исходных исключений; записывай только проверенные ID, enum и счётчики.
  Download-события принадлежат этапу получения remote-байтов, а не файловому
  writer, который уже получил `bytes`.
- До запуска writer на **существующих пользовательских данных** проверь
  data-root и отсутствие перенаправлений/конфликтующих операций. До рискованных
  миграций или cleanup нужен подтверждённый backup. Контракт сейчас только
  ручной quiescent offline: закрыть writers и БД, согласованно снять WAL/БД и
  managed-дерево, проверить manifest путей/размеров/SHA-256 и restore в
  изолированном каталоге ([backup-contract](../docs/plans/backup-contract.md)).
  Одна копия SQLite в WAL, несогласованный снимок или внешние `--out` байты не
  являются полной переносимой копией. Тесты используют disposable `tmp_path`,
  никогда не пишут в пользовательский data-root; derived cache можно очистить,
  но не историю и не artifacts. При непроверенном backup опасную операцию
  остановить, не обещая физического стирания данных.

## Managed-копии reference images (CN-01): файловый шов 🗂️

Решение [`release-scope.md`, change note `CN-01`](../docs/plans/release-scope.md)
распространяет managed-владельца на **reference images**: подготовленные входы Job
сохраняются управляемыми копиями в managed-дереве app data и связываются с Job
(относительный путь — в записи копии; позиция, `sha256`, MIME и размер — в `inputs`).

Владелец контракта — [`07-storage-history-costs.md`, раздел «Managed-копии
reference images»](../docs/plans/07-storage-history-costs.md); здесь только правила,
влияющие на файловые операции:

- **Путь:** `inputs/<job_id>/<position>.<ext>` — из Job ID и position, расширение
  из MIME проверенных байтов. Путь относительный, внутри managed root: без `..`,
  абсолютных/drive-relative путей и symlink/junction в существующих компонентах.
- **Публикация:** те же temp + flush/fsync, эксклюзивное создание имени и
  suffix-схема при конфликте, что у artifacts. Пользовательский файл не
  перезаписывается и не удаляется, а копия не пишется в `--out`.
- **Порядок (D03):** Job ID и предметная validation — до публикации; копии всех
  референсов и их связи сохраняются до платного POST. Ошибка чтения источника Job
  не создаёт; ошибка публикации запрещает submit.
- **Сбой после публикации:** файл не удаляется, submit запрещён, состояние
  сверяется по Job/пути/hash/size. Исключение после возможного DB commit не
  доказывает rollback: без сверки не повторять ни создание Job, ни оплаченный
  submit. Orphan возможен; фонового GC, cleanup-команды и backup CLI нет.
- **Backup:** managed-копии входов входят в тот же quiescent offline манифест, что
  и managed artifacts ([backup-contract](../docs/plans/backup-contract.md)).

**Реализовано:** `artifacts.inputs.LocalManagedInputStorage` реализует узкий порт
`ManagedInputStorage` и переиспользует E05 `publish_output`. До mkdir/publish
проверяет ID/position и bytes по существующему input probe/hash/size/MIME.
Resolve разрешает только форму `inputs/<positive-id>/<position>[suffix].<MIME-ext>`,
проверяет существующие ancestor/file symlink/junction, наличие и фактическую
целостность копии; до чтения сверяет stat-размер, чтение ограничено recorded
`size_bytes + 1` на случай роста после stat (без нового global cap). Исходник
не перечитывается и не служит fallback.

Application hook `inputs.archive.archive_reference_images` подтверждает CREATED
Job/snapshots и вызывает обязательный model validator до публикации; сохраняет
все links и сверяет полный возвращённый/прочитанный Job до transport-ready.
FS/DB не атомарны: при частичном отказе сохраняются все опубликованные файлы;
после возможного commit выполняется одна сверка известного ID, без retry/cleanup.
Первый эффект FS — mkdir `inputs/<job_id>` после preflight. Непубликуемые temp
очищает только E05 publisher; конфликты/исходники не удаляются.

Тесты `test_managed_inputs.py` используют реальные копии и Peewee, включая удаление
исходника, коллизии, redirects, частичные отказы, rollback/unknown commit и MockHTTP
через настоящий `PolzaProviderGateway`. `test_manual_managed_backup.py` проверяет
ручную quiescent процедуру DB+inputs+outputs, manifest и изолированный restore.
Это тестовое доказательство процедуры, не runtime backup API.

**Не реализовано:** полный E07/CLI, history show/retry/sync интеграция, backup CLI/GC.
Файловый шов не означает приёмки всего `CN-01` и не проверял пользовательские данные.
