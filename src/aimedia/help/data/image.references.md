---
topic: image.references
title: Reference images
summary: Долговечные проверенные копии входов
status: stable
related: [image.generate, jobs.history, jobs.retry]
---
# Reference images

```text
aimedia image generate --prompt "Keep the character" --image face.png --image clothes.webp --model qwen-image-2-1 --allow-experimental
```

Порядок `--image` сохраняется. MIME, размер и SHA-256 проверяются по байтам, не
расширению. Копии записываются в managed `inputs/<job_id>` до POST и связываются
с Job. `jobs show <id> --json` возвращает provenance, managed_path, hashes/MIME/size,
а `managed_inputs` содержит абсолютные пути для открытия даже после удаления исходника.
Retry читает проверенные копии, не удалённые исходники. Повреждённая копия — отказ.

Пользовательские исходники не удаляются и не меняются. Копии входов не пишутся в
`--out` и не являются artifacts результата. Полный backup требует quiescent DB,
managed inputs и outputs вместе; runtime backup/cleanup команд нет.
