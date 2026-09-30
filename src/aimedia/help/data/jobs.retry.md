---
topic: jobs.retry
title: Явный retry
summary: Новая платная попытка с lineage
status: stable
related: [jobs.sync, jobs.history, image.references]
---
# Retry

```text
aimedia jobs retry 1 --allow-experimental --json
```

Создаётся **новый** Job с relation `retry_of`. Prompt/параметры и проверенные managed
копии входов переносятся; старые remote ref, cost, usage, artifacts, status и error
не копируются. История исходного Job не меняется. Retry — явная новая платная
генерация, в отличие от sync. Произвольных overrides в v0.1 нет.
