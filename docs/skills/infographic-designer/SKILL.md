---
name: infographic-designer
version: "0.2.1"
description: >
  Создаёт, проверяет и точечно исправляет инфографику как визуальное объяснение:
  infographic, visual explanation, architecture diagram, process map, annotated
  object, timeline, comparison, data story, wireframe, infographic audit, repair.
  Используй для «сделай инфографику», «объясни визуально», «создай схему»,
  «проверь стрелки/вложенность/читаемость», «исправь только X», «объясни скрин»,
  «покажи куда нажать». Для UI включает screenshot-explainer с планированием и декомпозицией. Сохраняет
  источник, смысл связей, точный текст и числа; выбирает композицию и renderer,
  выдаёт видимый макет, редактируемый исходник, export и честный QA.
  Не предназначен для обычного текстового summary, фоторетуши, декоративных
  изображений или концепций YouTube thumbnail без объяснительной задачи.
argument-hint: create|audit|repair + source-or-artifact + optional rigor/stop_at
---

# Infographic Designer

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## Назначение

Один orchestrator ведёт визуальное объяснение через `create`, `audit` или `repair`.
Он последовательно выполняет роли смыслового проектировщика, архитектора,
рендерера и проверяющего. Разделение ролей не требует команды агентов.

Общий порядок: источник → смысл → композиция и видимый wireframe → стиль и
рендер → проверка → результат. Стрелка означает связь, контейнер — принадлежность,
число и подпись должны совпадать с источником. Эти требования действуют в обоих режимах.

## Каталог файлов

| Когда читать | Файл |
| --- | --- |
| Скрин интерфейса, выноски, куда нажать | [modules/screenshot-explainer/SKILL.md](modules/screenshot-explainer/SKILL.md) — отдельный короткий сценарий внутри пака |
| Всегда после выбора операции | [docs/working-modes.md](docs/working-modes.md) |
| Создание | [playbooks/create.md](playbooks/create.md) |
| Проверка готового изображения | [playbooks/audit.md](playbooks/audit.md) |
| Точечное исправление | [playbooks/repair.md](playbooks/repair.md) |
| Краткий пакет standard | [templates/job-brief.md](templates/job-brief.md) |
| Виды отношений и бюджет | [references/core/relation-types.yaml](references/core/relation-types.yaml), [references/core/semantic-budget.yaml](references/core/semantic-budget.yaml) |
| Выбор каркаса | [references/grammar/skeleton-router.yaml](references/grammar/skeleton-router.yaml), выбранная карточка в `references/grammar/skeletons/` |
| Связи и кодировки | [references/grammar/connectors.yaml](references/grammar/connectors.yaml), [references/grammar/encodings.yaml](references/grammar/encodings.yaml) |
| Стиль | [references/style/style-router.yaml](references/style/style-router.yaml), выбранный профиль в `references/style/profiles/` |
| Гибридный стиль | [references/style/hybrid-compatibility.yaml](references/style/hybrid-compatibility.yaml) |
| Рендер | [references/production/renderer-router.yaml](references/production/renderer-router.yaml) |
| QA | [references/qa/gates.yaml](references/qa/gates.yaml), [rules/registry.yaml](rules/registry.yaml) и применимые rule files |
| Structured artifacts | [contracts/](contracts/), [templates/](templates/) |
| Конкретная роль при необходимости | Curated `.md` в [prompts/](prompts/) |

Читай выбранный файл целиком; не загружай все профили, правила и шаблоны на каждый
запрос. Runtime опирается на curated файлы. Atlas, extracted `.generated.md`,
development source maps и extractor не являются обязательным контекстом исполнения.

## Режимы

Операция и глубина проверки выбираются независимо:

| Параметр | Значения | Смысл |
| --- | --- | --- |
| `route` | `create`, `audit`, `repair` | Что сделать с артефактом |
| `rigor` | `standard` (default), `strict` | Объём формализации и проверки |
| `stop_at` | `semantic_spec`, `wireframe`, `render_brief`, `draft`, `final`, `release_package` | Где завершить создание |

**Standard** объединяет план, source truth, семантику, композицию, стиль и renderer
в один `job-brief.md`. Требует видимый SVG/PNG wireframe, редактируемый исходник,
финальный export и короткий QA при полном создании. Structured contracts добавляются
там, где реально используются machine validators. Финальный независимый critic
привлекается при доступности.

**Strict** выбирается по явному запросу или при высоком риске: критические числовые
сравнения, последствия ошибки, сложная topology, обязательная проверка до публикации.
Нужны полные structured contracts и evidence, независимые G2 и финальная проверка.
Если независимый reviewer недоступен, продолжай подготовку и self-review, зафиксируй
ограничение; strict release не получает PASS до независимой проверки.

`stop_at=wireframe` не означает `standard`: возможен strict wireframe. Standard
может выпускать полный package. Политика остановок и состав пакета — в working modes.

## Быстрый алгоритм

Сначала распознай пояснение реального UI: для него открой встроенный модуль
`screenshot-explainer` из каталога выше и следуй его планированию, декомпозиции,
рендеру и QA. Его короткий план заменяет общий набор contracts/wireframe; остальные
шаги этого раздела относятся к общей инфографике.

1. Выбери операцию по запросу пользователя, `rigor` по риску и отдельный `stop_at`.
2. Найди источник, вопрос читателя, аудиторию, размер и существенные ограничения.
3. Зафиксируй confirmed facts, assumptions, unknowns, exact text и числа с единицами.
4. Выдели сущности, типы/направления отношений, один вопрос и takeaway. Проверь G1.
5. Выбери один primary skeleton и построй видимый монохромный SVG/PNG wireframe.
6. Проверь G2: направление, вложенность, порядок, кодировки и target size.
7. Выбери стиль и renderer; смысловая модель остаётся неизменной.
8. Создай редактируемый source и export. Выполни применимые deterministic checks G3.
9. Осмотри реальный render и ответь на comprehension questions по изображению G4.
10. Исправь дефекты, сохрани evidence и выдай пакет или артефакт выбранного `stop_at`.

Для `audit` начинай с существующего изображения и источника; возвращай QA без
создания нового visual. Для `repair` фиксируй before state и preserve/modify scope.
Продолжай уже авторизованную работу через внутренние gates без повторных user approvals.
Остановка требуется по `stop_at` либо при неизвестном критическом факте; отделяй
реальную недоступность инструмента от смыслового blocker.

## Каркас и renderer

| Вопрос | Primary skeleton |
| --- | --- |
| Что это и какие части важны? | `hero-callouts` |
| Из каких частей собран объект? | `exploded-axis` |
| Какие уровни образуют систему? | `layered-stack` |
| Что происходит шаг за шагом? | `linear-sequence` |
| Что меняется во времени или идёт параллельно? | `timeline` |
| Кто с кем взаимодействует? | `node-link` |
| Что принадлежит чему? | `nested-containers` |
| Чем варианты отличаются? | `small-multiples` |

Для времени/состояний сохраняй единицы, порядок, длительность и параллельность.
Runtime v0.2 выпускает статический timeline/sequence. Полные `adapt`, `series`,
`derive_template`, scrollytelling и exploratory interactive остаются вне scope;
при перегрузе предложи декомпозицию, не обещая готовый отдельный route.

| Renderer route | Когда и кто владеет смыслом |
| --- | --- |
| `diagram-first` | SVG/HTML/Mermaid/Graphviz владеют topology и точными подписями |
| `chart-first` | Chart library или SVG владеют данными, scale и annotation |
| `guided-generation` | Image model для объектного образа; exact text проверяется или накладывается отдельно |
| `hybrid-structured` | Structured skeleton + image layer + редактируемый text overlay |

Проверь доступные capabilities: генерация/редактирование изображений, SVG/HTML,
chart renderer, дизайн-инструмент, rasterizer. Навык не предполагает конкретного
collaborator. Для обработки результата в vault используй доступный
`image-card-processor`; для концепций YouTube thumbnail — соседний
`copywriting-youtube-thumbnail-concept`. Загрузи соответствующий skill при переходе.
Для пояснения интерфейса используй встроенный `modules/screenshot-explainer`.
Отдельное имя `screenshot-explainer` в каталоге skills — удобный прямой вход в тот же модуль.

Числовые пороги вроде «8 critical edges» — эвристики маршрутизации, а оценки
reliability — ожидания по классу renderer; они не доказывают надёжность результата.

## Gates и доказательства

G1 проверяет источник, вопрос, сущности, связи, текст, числа и unknowns. G2 проверяет
наблюдаемый wireframe. G3 сравнивает фактический render с brief/contracts: размер,
текст, значения, clipping, связи. G4 проверяет ответы на вопросы, путь по стрелкам,
target-size readability, accessibility и сохранение scope при repair.

Standard G4 — осмотр изображения и вопросы понимания. Model first-impression
проверка не является измеренным пятисекундным или человеческим тестом. Strict
добавляет подробный path tracing, teach-back и regression evidence; человеческое
тестирование фиксируется только если действительно проведено.

Self-review называется self-review. Независимый critic получает artifact, источник,
требования и rule IDs; его собственная оценка хранится отдельно. Для finding укажи
expected, observed, evidence, severity, minimal fix и confidence/unknowns. Если
визуальный осмотр недоступен, честно запиши `not_run`; структурная проверка файла
не доказывает читаемость изображения.

Вердикты: `pass | repair | rebuild | block`. Gate PASS G1/G2/G3 разрешает следующий
этап (`advance_to_next_stage`); release PASS требует G4 и применимые prerequisites.
Hard fail нельзя компенсировать Score.

## Структурные правила

- Один visual отвечает на один primary question; тема сама по себе не определяет каркас.
- Stable IDs сохраняются между стадиями и в structured artifacts.
- Контейнер означает реальную принадлежность; critical connector имеет тип и направление.
- В machine graph containment записывается `from=container`, `to=child`, `type=containment`; `nodes[].parent` отражает тот же факт. Не создавай `entity.parent`.
- Цвет подкреплён текстом, формой или паттерном; декоративные элементы не имитируют связи.
- Числа сохраняют значение, единицы, scale, порядок категорий, as-of и source.
- Style не меняет entity count, направления, containment, sequence, exact text или values.
- Количество этапов и колонок определяется содержанием. Style profile не добавляет этапы.
- YAML wireframe описывает граф, но не заменяет видимый SVG/PNG макет.
- Не уменьшай текст ради перегруженного canvas: сократи или раздели содержание.
- Repair меняет только согласованный modify scope; сохранение preserve доказывается сравнением.

## Инструменты проверки

Зависимости: [requirements.txt](requirements.txt). Используй валидаторы для
соответствующих structured artifacts, не заставляя простой standard job создавать
контракты лишь ради количества файлов:

- `scripts/validate_contract.py` — schema YAML/JSON artifact;
- `scripts/validate_graph.py` — entities/edges/direction/containment;
- `scripts/validate_preserve_contract.py` — structured before/after scope;
- `scripts/validate_package.py` — проверка пакета навыка перед установкой, не visual QA.

## Чеклист готовности

- [ ] Route, rigor и stop_at выбраны независимо; неизвестные критические факты разрешены?
- [ ] Вопрос, сущности, связи, точный текст и числа сохранены?
- [ ] G1 и G2 проверены до стилизации; видимый wireframe существует?
- [ ] Renderer подходит для фактического риска и доступен в среде?
- [ ] Редактируемый source и export существуют для завершённого create/repair?
- [ ] Реальный render осмотрен в target size, вопросы отвечены по изображению?
- [ ] QA отличает machine checks, self-review, independent review и not_run?
- [ ] Strict independent G2/final выполнены либо release явно ожидает их?
- [ ] Repair сохраняет preserve; пакет и заявление о готовности соответствуют stop_at?
