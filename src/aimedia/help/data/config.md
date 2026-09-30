---
topic: config
title: Локальная конфигурация
summary: TOML и secret reference
status: stable
related: [getting-started, cli.json]
---
# Настройки

```text
aimedia config init --file settings.toml
aimedia --config settings.toml config validate --json
aimedia --config settings.toml config show --json
```

TOML содержит `polza_api_key_env = "POLZA_API_KEY"`, `log_level = "INFO"` и при
необходимости `data_dir`. Сам ключ — только в переменной окружения, не в TOML/argv.
`config init` создаёт новый файл эксклюзивно, никогда не перезаписывает существующий.
`show`/`validate` — локальные, не проверяют авторизацию в сети.

Приоритет CLI → выбранный TOML → AIMEDIA_* env → defaults. `--data-dir` или
`AIMEDIA_DATA_DIR` задаёт изолированный root истории/inputs/outputs. По умолчанию
используются platformdirs и системный `settings.toml`, если существует.
Установленная программа не загружает `.env` из cwd; development-загрузка только явно.
Миграции последовательны: v1/v2 DDL неизменны, v3 добавляет derived FTS5 по DB snapshots,
не перечитывая legacy файлы. Перед ручной рискованной операцией нужен проверенный backup.
