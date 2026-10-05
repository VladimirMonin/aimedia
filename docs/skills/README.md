# Пакет навыков aimedia

Пакет находится в `docs/skills/` репозитория. Это инструкции для агента,
**не** встроенная справка CLI и **не** часть опубликованного `uv tool` v0.1.0.
Само расположение `docs/skills/` не включает autodiscovery Pi.
В каталоге три корневых пакета и все четыре `SKILL.md`:

| Навык | Роль |
|---|---|
| [aimedia](aimedia/SKILL.md) | Корневой: установленный image CLI, генерация и референсы, модели/история, расходы |
| [visual-story-director](visual-story-director/SKILL.md) | Корневой: текстовое предпроизводство истории, персонажей, стиля, раскадровки и промптов; без генерации image/audio/video |
| [infographic-designer](infographic-designer/SKILL.md) | Корневой управляющий master: создание, аудит и точечное исправление визуальных объяснений; выбирает сценарий и ресурсы |
| [screenshot-explainer](infographic-designer/modules/screenshot-explainer/SKILL.md) | Вложенный модуль master: планирование, декомпозиция и аннотация реального UI-скриншота; не управление ОС |

## Как использовать

Передайте агенту путь к нужному корневому `SKILL.md` и задачу; пусть прочитает
этот файл целиком. Для standalone использования копируйте **целиком папку
выбранного корневого пакета**, сохраняя относительные ресурсы. `aimedia` требует
установленную команду `aimedia`, но не checkout, dev-окружение или остальные навыки.

В Pi явно подключайте точный файл из корня репозитория (это примеры, не запуск):

```text
pi --skill ./docs/skills/aimedia/SKILL.md
pi --skill ./docs/skills/visual-story-director/SKILL.md
pi --skill ./docs/skills/infographic-designer/SKILL.md
```

Вне репозитория передайте фактический полный путь к скопированному `SKILL.md`.
Флаг повторяемый; подключённое содержимое доступно через `/skill:aimedia`,
`/skill:visual-story-director` или `/skill:infographic-designer`.
Регистрация здесь — документационная: global/project settings, `.pi/` и
`.agents/` не меняются. Перед использованием проверяйте содержимое;
scripts — код, а не разрешение на запуск или установку зависимостей.

### Master и вложенный модуль

Обычный вход — только корневой `infographic-designer/SKILL.md`, не весь каталог.
Master использует [routes.yaml](infographic-designer/routes.yaml), выбирает
`create`, `audit` или `repair`, затем читает
[working modes](infographic-designer/docs/working-modes.md), нужный playbook и
только применимые references, профиль и правила. Это progressive disclosure:
выбранный документ читается целиком, весь пак заранее загружать не нужно.
Один агент последовательно выполняет роли смыслового проектировщика, архитектора,
рендерера и проверяющего; это не автоматический fanout.

Для реального UI master сначала направляет в зарегистрированный выше
`screenshot-explainer` и его [procedure](infographic-designer/modules/screenshot-explainer/docs/procedure.md).
Модуль уже внутри пакета; отдельная установка, копия в соседнем root или второе
независимое управление не нужны. Его отдельная строка служит обнаружению и
документации, не обязательному второму `--skill`.

Self-review не становится независимым от смены роли или промпта. В `standard`
отсутствие отдельного reviewer явно раскрывается; в `strict` независимые G2 и
финальный review обязательны для release PASS. Если их нет, отметьте проверки
`not_run`, подготовьте кандидат и не заявляйте strict release PASS.
Отдельный независимый review выполняется только при доступности и разрешении;
импорт навыка не разрешает автоматически запускать других агентов.

## Передача и доступные инструменты

`visual-story-director` передаёт prompt или задание на референс, не разрешение
на платный запрос. `aimedia` отдельно согласует модель, параметры, реальные
входные файлы и бюджет. `voice-script` — только текст для отдельного исполнителя:
aimedia не выполняет TTS, аудио, видео, монтаж, text generation или embeddings.

Перед выбором renderer проверьте фактически доступные инструменты и навыки.
Донор ссылается на `imagegen`, `image-card-processor` и
`copywriting-youtube-thumbnail-concept`; эти внешние возможности не поставляются
этим каталогом и не считаются установленными. `aimedia` может выполнять явно
согласованный этап генерации изображений, но не заменяет автоматически bitmap
editor, SVG/HTML/chart renderer, rasterizer или систему редактируемых слоёв.
Не обещайте точные UI/pixel edits, слои, editable source или финальный render без
реального инструмента и проверки результата. Недоступный этап — `not_run`,
не PASS; импорт не даёт разрешения на платный API и не расширяет image-only CLI.

## Состав и происхождение

`visual-story-director` 1.3.0 импортирован целиком и без редактирования
из принятой пользовательской редакции. Обновление описано в
[отчёте](../plans/progress/visual-story-v1.3-2026-10-05.md).
Сохраняются [README](visual-story-director/README.md),
[Apache-2.0 LICENSE](visual-story-director/LICENSE),
[THIRD_PARTY_NOTICES](visual-story-director/THIRD_PARTY_NOTICES.md),
[licenses](visual-story-director/licenses/) и
[provenance](visual-story-director/provenance/upstream-lock.json), а также все
scripts/tests/examples. Донорские отчёты и evals — сведения донора, не доказательство
проверки aimedia или исполнения scripts в этом репозитории.

`infographic-designer` 0.2.1 — пользовательский пакет, перенесённый по объявленному
[runtime export](infographic-designer/docs/runtime-package.md): ровно 115 файлов
из `distributions.runtime.files` в [manifest](infographic-designer/manifest.yaml).
114 файлов сохранены побайтно; единственное документированное преобразование —
`distribution: development` → `distribution: runtime` в manifest. Literal
allowlist сохранён. Development inbox/provenance, evals, source-map,
`.generated.md`, логи и Python cache не импортированы. Корневые README и
LICENSE/NOTICE в исходном пакете не обнаружены; лицензию ему не назначаем,
Apache-2.0 соседнего навыка на него не переносим. Публикация пользовательского
пакета разрешена владельцем; это не перелицензирование чужих материалов.
Структурная проверка импорта не означает creative eval, запуск донорских scripts
или независимый PASS конкретной инфографики.

## Расширение

Добавляйте корневые пакеты соседями `<name>/SKILL.md`; вложенные модули сохраняйте
внутри master и регистрируйте **каждый** `SKILL.md` с ролью и фактическим путём.
Ресурсы остаются относительными внутри пакета. Сохраняйте лицензии и уведомления.
Не помещайте в пакет пользовательские проекты, prompts, референсы, outputs,
историю, конфигурацию или секреты; они хранятся в выбранных внешних местах.
Правила импорта и сопровождения — [DOCS.SkillPack](../../instructions/DOCS.skill-pack.instructions.md).
