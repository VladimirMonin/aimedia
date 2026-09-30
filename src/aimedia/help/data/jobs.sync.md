---
topic: jobs.sync
title: Sync и recovery
summary: Продолжение известного remote execution без submit
status: stable
related: [jobs.retry, jobs.history, image.generate]
---
# Sync

```text
aimedia jobs sync 1 --json
aimedia jobs sync --all --concurrency 3 --json
```

Sync делает только GET по сохранённым remote ID/operation, **никогда POST**.
Без ID синхронизируются Jobs с известным remote ref. Timeout, interrupt и локальный
отказ финализации допускают recovery того же remote execution. При running observation
FAILED не становится RUNNING и прежняя ошибка сохраняется. После полной финализации
FAILED → COMPLETED сохраняет `recovery.previous_error`.

`ok` и exit code относятся к текущей попытке sync, не к прежней ошибке Job.
Успешное наблюдение running для старого FAILED — `ok:true`/0. Новый provider отказ,
remote failed или local finalization failure — `ok:false` и соответственно 4, 5, 7;
сохранённый прежний error не подменяет ошибку текущей попытки в envelope.

Повторный sync COMPLETED — local no-op: без нового artifact или повторного расхода.
Persisted partial artifacts переиспользуются. No-clobber защищает опубликованные файлы.
Неизвестный исход submit без ref не восстанавливается через sync: никакого скрытого
платного retry. Ctrl+C/timeout не означают remote cancellation.

Kernel-held локальный Job lock защищает два процесса; ОС снимает ownership при crash.
Lock-файл не удаляется. Опубликованный файл после unknown DB commit сохраняется:
отказ acknowledgement не доказывает rollback. Автоматического orphan cleanup нет.
