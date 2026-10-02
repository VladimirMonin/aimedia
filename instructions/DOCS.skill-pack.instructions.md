---
applyTo: "docs/skills/**,AGENTS.md,README.md"
name: "DOCS.SkillPack"
description: "Читай при добавлении, импорте или изменении docs/skills/ и навигации в AGENTS.md/README.md: корневые master и вложенные SKILL.md, progressive disclosure, полный или объявленный runtime export донора, лицензии и границы image-only CLI."
---

# DOCS — Пакет навыков

Владелец — самостоятельный агентский пакет в [docs/skills/](../docs/skills/README.md).
Он не заменяет [packaged help](HELP.atomic-resources.instructions.md), не входит
в опубликованный uv tool v0.1.0 и не обнаруживается Pi автоматически лишь потому,
что лежит в `docs/skills/`. Подключение — чтение конкретного `SKILL.md` или явный
`--skill <path>`; сопровождение не разрешает autoinstall, изменение global/project
settings или установку зависимостей.

## Структура и навигация

- Новый корневой пакет — сосед `<name>/SKILL.md`: YAML `name` (lowercase
  kebab-case, до 64 символов) и непустое точное `description` (до 1024 символов).
  Имена всех навыков уникальны. Дополнительные поля donor front matter,
  допускаемые Pi, не являются поводом переписывать импорт.
- Иерархический пакет имеет корневой управляющий master и вложенные модули
  с собственными `SKILL.md`. Сохраняй фактическую структуру; не превращай модуль
  в соседний root или второго orchestrator. Ресурсы и ссылки относительны внутри
  пакета. Для `aimedia` нужен установленный CLI, не checkout или соседний навык.
- В `docs/skills/README.md` регистрируй **все** обнаруженные рекурсивно `SKILL.md`
  с точными путями и ролями master/module; различай число root-пакетов и навыков.
  Корневые `AGENTS.md` и `README.md` ведут к каталогу. Обновляй навигацию вместе.
- Обычный вход и явный `pi --skill` для иерархического пакета — корневой master.
  Он выбирает route/playbook/module и раскрывает только применимые resources
  постепенно (progressive disclosure); выбранный документ читается целиком,
  весь пакет заранее не нужен. Регистрация модуля не требует второй установки.
- Роли обычно выполняет один агент последовательно, не автоматический fanout.
  Self-review не независимый review: если strict-контракт требует отдельного
  reviewer, отсутствие отмечается `not_run` и блокирует strict release PASS,
  а не подготовку кандидата. Отдельный review требует доступности и разрешения.
- Источник актуальных параметров — установленный executable version/`--help`,
  resolved `help --raw` и `models show --json`, не переписанный каталог в навыке.

## Импорт и лицензии

Перед первой записью проверь исходник и destination на symlink/junction/reparse
в дереве и существующих компонентах пути, private данные и условия использования.
Внешний donor — read-only. Если профиль поставки не объявлен, копируй **всю**
папку, включая LICENSE, notices, licenses, provenance, scripts/tests/examples,
без исправления текстов или EOL. Если donor явно определяет runtime export,
используй согласованный literal allowlist его manifest, не придуманный фильтр.
Зафиксируй полный исходный inventory и одобренный manifest, проверь наличие всех
allowlisted файлов, дубликаты (включая case collisions), безопасные относительные
пути и отсутствие выхода за root. Не экспортируй private development/cache
материалы, исключённые объявленным профилем. Не меняй уже принятый полный импорт
соседнего пакета ради нового профиля.

Разрешены только документированные и согласованные преобразования export;
зафиксируй точный byte diff и semantic diff manifest, сохрани literal allowlist.
Остальные файлы должны совпасть побайтно. Surrounding index объясняет
ограничения, а не переписывает donor. Лицензии и уведомления сохраняй; если профиль
исключает применимое уведомление, остановись и согласуй, не выбрасывай его молча.
Отсутствие LICENSE/NOTICE фиксируется как факт, не право назначить лицензию;
пользовательский пакет с явным разрешением публикации не перелицензируется.
Неясные ограничения третьих лиц или private content — STOP до решения владельца.

Зафиксируй полный file/directory inventory, размеры и SHA-256 **raw bytes**
источника до/после операции и destination, expected transformations и exclusions.
Изменение источника, расхождение копии или несогласованная трансформация — STOP.
Git text normalization не должна менять imported bytes: допустим только узкий
`.gitattributes` override для конкретного donor subtree; проверь clean-filter
представление против raw bytes до staging, затем staged blobs проверяет интегратор.
Не запускай imported scripts без отдельного аудита и разрешения; структурная
валидация и hash identity не означают creative eval или script PASS.

## Границы использования

- `visual-story-director` готовит только текст предпроизводства, включая
  voice-script; это не audio/TTS/video/render capability aimedia. Handoff prompt
  или reference brief не даёт разрешения на paid generation.
- Master/module могут ссылаться на внешние навыки и renderer capabilities:
  проверяй их реальную доступность, не объявляй установленными из-за упоминания.
  aimedia может быть согласованным image generation stage, но не автоматически
  bitmap editor, SVG/chart renderer или система editable layers. Точные UI edits,
  сохранность пикселей/слоёв и visual QA требуют реального инструмента/evidence.
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
JSON/schema syntax, AST импортированного Python без исполнения, route/module
entrypoints и manifest parity, команды/flags по actual help/metadata, точность
inventory/hashes, frozen соседние пакеты и Git diff scope. Full schema semantic
validation выполняй при доступном проверенном validator без установки;
недоступность — NOT_RUN, не PASS синтаксиса. Сохраняй argv/cwd/raw exit codes,
manifest, patch и ограничения в ignored evidence; без секретов/private prompts.
Installed local smoke выполняй вне checkout с собственными fresh config/data,
secret-name scrub (включая alias) и socket child guard по
[TEST.OfflineQuality](TEST.offline-quality.instructions.md).
Paid примеры проверяй структурно/через `--help`, не исполняй.
Широкий runtime/release/live gate для чистого docs-среза не подменяй focused
проверкой: явно укажи NOT_RUN и оставь frozen runtime/tag без изменений.
