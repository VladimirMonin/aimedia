---
topic: getting-started
title: Первый запуск
summary: Настройка и одна генерация
status: stable
related: [config, image.generate, models.capabilities, cli.json]
---
# Первый запуск

Локальная Python 3.12 image-only утилита через Polza. Справка, модели, история и
расходы работают без API key и без сети. Платная генерация — только явная команда.

```text
aimedia version --json
aimedia models list --json
aimedia config init
aimedia config show --json
```

`config init` не перезаписывает существующий TOML. Ключ хранится только в переменной
окружения `POLZA_API_KEY`, не в файле, argv или истории. В Windows задайте её через
системные настройки среды; в POSIX — через используемый вами secret manager.
Установленный инструмент не ищет `.env` в текущем каталоге.

```text
aimedia image generate --prompt "A watercolor laboratory robot" --model qwen-image-2-1 --allow-experimental --format webp
aimedia jobs recent
aimedia jobs show 1 --json
```

Встроенные модели документированы официальным каталогом, но **не проверены live**.
Требуется явный `--model` и `--allow-experimental`. Каталожная цена не гарантирует
будущий тариф; фактическая стоимость берётся из ответа provider.
