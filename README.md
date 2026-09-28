# aimedia

Локальный CLI для генерации изображений через Polza.

> Проект находится на этапе E01: создан запускаемый каркас пакета. Команды
> генерации, registry, хранения и справки ещё не реализованы — они появятся на
> следующих этапах плана.

## Установка для разработки

Требуется [uv](https://docs.astral.sh/uv/) и Python 3.12.

```bash
uv sync --locked
```

## Запуск

```bash
uv run --locked --no-env-file aimedia --help
uv run --locked --no-env-file aimedia version --json
```

Команды `--help` и `version` работают без API key, базы данных и сетевых
запросов.

## Проверки

```bash
uv run --locked --no-env-file python scripts/quality.py quick
uv run --locked --no-env-file python scripts/quality.py full --report-dir artifacts/quality
```

## План

Полный план разработки, границы релиза и решения по развилкам — в
[`docs/plans/README.md`](docs/plans/README.md).
