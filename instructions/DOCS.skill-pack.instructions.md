---
applyTo: "docs/skills/**,AGENTS.md,README.md"
name: "DOCS.SkillPack"
description: "Читай при добавлении, импорте или изменении docs/skills/ и его навигации в AGENTS.md/README.md: самостоятельные SKILL.md, точная копия доноров, лицензии, границы image-only CLI и безопасная проверка примеров."
---

# DOCS — Пакет навыков

Владелец — самостоятельный агентский пакет в [docs/skills/](../docs/skills/README.md).
Он не заменяет [packaged help](HELP.atomic-resources.instructions.md), не входит
в опубликованный uv tool v0.1.0 и не обнаруживается Pi автоматически лишь потому,
что лежит в `docs/skills/`. Подключение — чтение конкретного `SKILL.md` или явный
`--skill <path>`; сопровождение не разрешает autoinstall, изменение global/project
settings или установку зависимостей.

## Структура и навигация

- Новый навык — сосед `<name>/SKILL.md`: YAML `name` (lowercase kebab-case,
  до 64 символов) и точное непустое `description` (до 1024 символов).
- Навык переносится одной своей папкой; дополнительные ресурсы и ссылки на них
  относительны и остаются внутри неё. Для `aimedia` нужен установленный CLI,
  а не checkout/dev tests или соседний навык.
- Краткий индекс и роли — в `docs/skills/README.md`; маршруты к пакету — в
  корневых `AGENTS.md` и `README.md`. Обновляй их в том же логическом изменении.
- Источник актуальных параметров — установленный executable version/`--help`,
  resolved `help --raw` и `models show --json`, не переписанный каталог в навыке.

## Импорт и лицензии

Перед первой записью проверь исходник и destination на symlink/junction/reparse
в дереве и существующих компонентах пути, неожиданные private данные и лицензии.
Внешний donor — read-only. Копируй **всю** папку, включая LICENSE, notices,
licenses, provenance, scripts/tests/examples, без исправления донорских текстов
или EOL. Surrounding index объясняет ограничения, а не редактирует donor.

Зафиксируй полный file/directory inventory, размеры и SHA-256 **raw bytes**
до/после копирования у источника и destination. Изменение источника во время
операции, расхождение копии, непонятная лицензия или private data — STOP.
Git text normalization не должна менять imported bytes: допустим только узкий
`.gitattributes` override для конкретного donor subtree; проверь clean-filter
представление против raw bytes до staging, затем точные staged blobs проверяет
интегратор. Уведомления и лицензии нельзя выбрасывать ради уменьшения diff.
Не запускай imported scripts без отдельной проверки безопасности; структурная
валидация и hash identity не означают creative eval или script PASS.

## Границы использования

- `visual-story-director` готовит только текст предпроизводства, включая
  voice-script; это не audio/TTS/video/render capability aimedia. Handoff prompt
  или reference brief не даёт разрешения на paid generation.
- aimedia остаётся image-only. Реальные референсы создаются обычным paid
  `image generate`, проверяются просмотром final и решением пользователя,
  затем actual path передаётся через `--image`. Нельзя выдумывать approval/файл.
- Разделяй каталог моделей (`models list/show`, host JSON filtering) и DB-only
  историю (`jobs search` FTS5, без OCR/visual similarity/filesystem scan).
- Paid план требует явного согласия и бюджета всего batch/осознанных retries;
  uncertain submit не повторяется автоматически. Sync продолжает известный ref
  без POST, retry — новый paid Job. Цена каталога не actual и не account balance;
  Decimal/валюты, zero/unknown и выбранная история различаются.
- Источник persistent данных — выбранный пользователем config и data-root,
  final artifacts и managed inputs, **не папка навыка**. Пользовательские projects,
  prompts, изображения, outputs, SQLite, config, секреты и raw logs внутрь пакета
  не пишутся. Обычное использование вправе читать существующую выбранную историю.

## Проверка документационного среза

Проверь YAML/front matter, относительные ресурсы и новые Markdown-ссылки,
JSON/schema syntax, команды/flags по actual help/metadata, точность imported
inventory/hashes и Git diff scope. Сохраняй argv/cwd/raw exit codes и manifest
кандидата в ignored evidence; без секретов и private prompts.
Installed local smoke выполняй вне checkout с собственными fresh config/data,
secret-name scrub (включая alias) и socket child guard по
[TEST.OfflineQuality](TEST.offline-quality.instructions.md).
Paid примеры проверяй структурно/через `--help`, не исполняй.
Широкий runtime/release/live gate для чистого docs-среза не подменяй focused
проверкой: явно укажи NOT_RUN и оставь frozen runtime/tag без изменений.
