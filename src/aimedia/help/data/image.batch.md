---
topic: image.batch
title: Batch
summary: Независимые Jobs с ограниченной параллельностью
status: stable
related: [image.generate, jobs.history, jobs.sync]
---
# Batch

```text
aimedia image batch prompts/*.md --model qwen-image-2-1 --allow-experimental --concurrency 3 --format webp --json
```

Каждый positional UTF-8 prompt-файл — отдельный Job. Glob раскрывается программой
в детерминированном порядке, в том числе на Windows. Источники читаются до создания
Jobs. Общие image/model/resolution/aspect-ratio/format/out/name параметры применяются
ко всем элементам. Нет CSV/YAML manifests.

Semaphore удерживается на всём submit/poll/download/finalization, не только POST.
Ожидаемые Job failures не отменяют соседей. JSON содержит total/completed/failed/jobs;
exit 9 означает partial batch failure. Ctrl+C прекращает локальные tasks: ожидающие
слота CREATED становятся CANCELLED, известные remote refs сохраняются для sync.
