---
topic: jobs.history
title: История, поиск и расходы
summary: SQLite snapshots после перезапуска
status: stable
related: [jobs.retry, jobs.sync, cli.json, image.references]
---
# История

```text
aimedia jobs recent --limit 20 --status failed --json
aimedia jobs show 1 --json
aimedia jobs search "laboratory robot" --status completed --json
aimedia jobs costs --today --json
aimedia jobs costs --month --json
```

`jobs show` читает полный сохранённый Job: prompt и источники, параметры, input
provenance/managed paths/hashes/MIME/size, remote ref/operation, artifacts, status,
error, usage и cost. Модель не обязана оставаться в текущем каталоге.

Поиск — лексический SQLite FTS5 по сохранённым SQLite snapshots: compiled prompt,
model/provider, prompt source paths, reference provenance/имена/managed paths/SHA-256,
result artifact имена/local paths/SHA-256 и metadata (например MIME, роль, dimensions,
labels). Находит связанные Jobs, даже после удаления исходников. Файлы и изображения
при поиске/backfill не читаются; OCR, embeddings и семантического поиска нет.
Пробелы разделяют обязательные слова, не SQL/семантический язык. `--limit` 1–1000,
`--status` фильтрует историю. `jobs list` — alias `recent`.

Деньги — точные Decimal-строки. RUB и USD считаются раздельно, неизвестная цена
не равна нулю. `--today`/`--month` задают UTC-период создания Job; без них — вся
история. Failed Jobs с фактической стоимостью включаются. Локальные команды
не требуют API key, не выполняют автоматический sync.
