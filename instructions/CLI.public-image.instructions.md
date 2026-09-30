---
applyTo: "src/aimedia/cli/**,src/aimedia/config.py,src/aimedia/bootstrap.py,tests/cli/**"
name: "CLI.PublicImage"
description: "Читай при изменении публичного image/history/models/config CLI, argv/global flags, JSON/exit codes и installed entrypoint: ordered prompt sources, один stdout JSON и локальные команды без API."
---

# CLI — Публичная image утилита

Источник контракта — [04](../docs/plans/04-cli-contract.md) и
[release-scope](../docs/plans/release-scope.md). Владелец argv/presentation —
`cli.runtime.PublicGroup`, команд — `cli.commands`, composition — `bootstrap`.
CLI не выполняет HTTP/ORM запросы напрямую и не реализует polling.

- `--json`/`--provider` принимаются до домена и у конечной команды; одинаковые
  повторы допустимы, конфликт — 2. Parser сохраняет interleaved prompt sources
  одним ordered списком, не склеивает два независимых списка. Не логировать argv.
- В JSON mode весь stdout — один envelope, включая argv/config/domain/provider/
  filesystem/storage ошибки. Parser errors не печатают исходные значения; ANSI,
  progress и diagnostics не идут в stdout. Decimal строкой, UTC ISO timestamps,
  неизвестные концептуальные поля null. Secrets redacted до presentation.
- Exit codes: 0 success, 1 unexpected, 2 argv, 3 validation, 4 provider/config,
  5 remote failure, 6 timeout, 7 local files/artifacts, 8 storage, 9 partial batch,
  130 interrupt; 130 не означает remote cancel.
- Команды: image generate/batch; jobs recent (list alias)/show/search/retry/sync/costs;
  models list/show; providers list; config init/show/validate; help/version/--help.
  Нет audio/server/MCP/maintenance/detach или произвольного provider JSON.
- Явный model обязателен. Documented experimental модели требуют
  `--allow-experimental`; это выбор пользователем, не заявление live verification.
  Local --format отделён от provider output; явный --out уже существует и
  preflight выполняется до POST. --keep-original — отдельная роль, no-clobber.
  --format имеет parser choices png/jpeg/webp/jpg: иной формат — 2 без Job;
  предметная validation по-прежнему выполняется после CREATED (D03).
- Help/version/models/providers/history/costs/config не требуют ключа или API.
  History читает snapshots без текущего resolver; show даёт абсолютные locators
  managed copies после удаления исходника. Нет автоматического startup sync.
  Search находит Jobs по DB-only prompts, reference provenance/managed paths/hashes
  и result artifact paths/hashes/metadata; файлов/OCR/embeddings он не читает.
  Sync presentation использует текущий `SyncResult.error`, а не старый `Job.error`:
  новое provider/remote/local failure — false/4/5/7, old FAILED running observation — true/0.
  Ожидаемые SQLite connect/search ошибки нормализуются в storage, не ORM в CLI;
  неожиданные programming exceptions остаются INTERNAL/1.
- Config хранит только secret reference; ключ не передаётся в argv/TOML/БД/logs.
  CLI→TOML→AIMEDIA_*→defaults; системный settings.toml читается если существует,
  .env из cwd не читается. config init эксклюзивен, redirects запрещены.

Источник истины persistent данных — user TOML и SQLite/managed tree, не cache.
Первая запись config init — mkdir собственного config parent, затем exclusive file;
существующий файл не меняется. Тесты config/CLI используют tmp roots; тесты
default discovery подменяют `paths.user_settings_file` на disposable TOML, а
secret alias проверяется только synthetic canary без чтения реального env key. Рискованная
ручная операция с существующими данными требует backup/manifest/restore по
[DATA](DATA.sqlite-history.instructions.md); cleanup пользовательских данных нет.

Проверки — публичные argv/subprocess/MockHTTP + реальные SQLite/FS в tests/cli;
единый `scripts/quality.py full`, затем build/install smoke вне cwd и review.
