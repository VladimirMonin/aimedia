---
applyTo: "src/aimedia/artifacts/**,src/aimedia/application/artifact_finalization.py,tests/integration/test_artifact_*.py,tests/integration/test_output_collisions.py,tests/security/test_output_names.py"
name: "PROCESSING.ImageArtifacts"
description: "Читай при изменении image artifact storage/finalization: декодирование PNG/JPEG/WebP, managed и --out пути, no-clobber публикация, ошибки и согласованность файлов с Job history."
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
