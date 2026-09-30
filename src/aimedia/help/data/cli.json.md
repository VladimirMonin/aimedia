---
topic: cli.json
title: JSON и exit codes
summary: Машинный контракт stdout
status: stable
related: [jobs.history, image.generate, config]
---
# JSON

`--json` и `--provider` принимаются до домена и после конечной команды; одинаковые
повторы допустимы, конфликт provider — exit 2. Все stdout — ровно один JSON document
для успеха и ошибки, включая argv errors. Диагностика только stderr. ANSI/progress нет.

Успех: `{"ok":true,"data":...}`. Ошибка: `{"ok":false,"error":{"code":...,"message":...,"details":{}}}`.
Job failures также возвращают data с сохранённым Job; partial batch — data.jobs.
Неизвестные концептуальные поля null. Деньги строкой, timestamps ISO-8601 UTC.

Exit codes: 0 success; 1 internal; 2 argv; 3 validation; 4 provider/config;
5 remote failure; 6 timeout; 7 local files/artifacts; 8 database; 9 partial batch;
130 interrupt (не remote cancel). `--quiet`, `--verbose`, `--no-color` не меняют JSON.

`aimedia help <topic> --raw` выдаёт готовый Markdown без front matter и placeholders.
JSON help содержит тот же markdown вместе с topic/title/summary/status/related.
