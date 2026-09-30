---
topic: image.generate
title: Одна генерация
summary: Prompt, параметры и локальные результаты
status: stable
related: [image.references, image.batch, jobs.sync, models.capabilities]
---
# Одна генерация

```text
aimedia image generate --prompt-file base.md --prompt "Full body" --prompt-file scene.md --model qwen-image-2-1 --allow-experimental --resolution 1K --aspect-ratio 1:1 --format webp --name robot
```

`--prompt` и `--prompt-file` объединяются строго в порядке argv, разделитель — два
перевода строки. Файлы — UTF-8. Ошибка чтения не создаёт Job; предметная validation
создаёт FAILED Job, но не платный POST. `--image` повторяется для каждого reference.

`--format` — локальный png/jpeg/webp (`jpg` = jpeg), не provider output.
Другой формат — синтаксическая ошибка argv (exit 2), без чтения источников/создания Job.
`--out` требует уже существующий доступный каталог. По умолчанию результат в
managed `outputs/<job_id>`. Нет скрытой второй копии; no-clobber suffix при конфликте.
`--keep-original` сохраняет отдельный ORIGINAL. `--name` — безопасное базовое имя.

`--max-images` — outputs одного submit, не количество Jobs. Применяется лишь когда
модель объявляет такой параметр. У текущих двух моделей unit parameter не опубликован:
значения больше 1 отклоняются до POST. `--seed`/`--quality` также не принимаются без
документированной поддержки. `models show <id> --json` показывает реальные ограничения.

`--poll-interval` (по умолчанию 1 секунда) и `--wait-timeout` (300 секунд) конечны.
Timeout/Ctrl+C прекращают локальное ожидание, не отменяют remote execution.
Неизвестный исход POST никогда не повторяется автоматически.
