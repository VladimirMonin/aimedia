# Create — от источника к визуальному объяснению

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## 1. Нормализуй запрос и глубину проверки

Выбери route=create, rigor=standard по умолчанию или strict по риску/запросу,
и отдельный stop_at. Для готовой инфографики default stop_at=release_package.
Порядок, состав пакета и независимость: [working modes](../docs/working-modes.md).

Зафиксируй вопрос читателя, аудиторию, носитель, размер, источники и точные
требования. В standard используй [job brief](../templates/job-brief.md). В strict
создай production contract, content packet и остальные structured contracts по
schemas. Не требуй новых пользовательских approvals между внутренними стадиями
уже авторизованного create.

Отдели обязательные export constraints от предпочтений: точный размер/ratio
становится hard requirement только если этого требует запрос или целевой носитель.

## 2. Собери source truth и смысловую модель — G1

Отдели confirmed claims, assumptions и unknowns. Для каждой обязательной сущности
назначь stable ID, для каждой связи — from, to, type и meaning. Сохрани exact text,
значения, единицы, scale, порядок категорий, as-of и source refs.

Используй [semantic designer](../prompts/semantic-designer.md),
[relation types](../references/core/relation-types.yaml) и
[semantic budget](../references/core/semantic-budget.yaml). Standard хранит модель
в brief; strict — в semantic spec. Если graph validation реально используется,
создай structured spec из той же модели, укажи его как canonical в brief и рендери
из него. Не веди две разные topology truth sources.

G1 проверяет один primary question, takeaway, abstraction level, entities,
typed relationships, include/exclude, text/data и заранее заданные comprehension
questions. Неизвестный обязательный факт блокирует фактическое продолжение;
необязательное предположение можно явно отметить или исключить. Бюджет — эвристика:
при фактическом перегрузе сократи/раздели модель, не уменьшая текст.

## 3. Выбери композицию и создай видимый wireframe — G2

Выбери один primary skeleton по
[skeleton router](../references/grammar/skeleton-router.yaml). Рассмотри до трёх
кандидатов только если выбор неоднозначен; не заполняй сравнительную таблицу ради
одного очевидного процесса. Secondary решает локальную задачу.

Определи entry point, reading order, endpoint и кодировки. Создай монохромный
wireframe.svg или wireframe.png с реальными расположением, подписями, стрелками
и контейнерами. YAML wireframe — дополнительный graph artifact, не замена
видимой композиции.

Для topology-critical graph при machine validation нужны compatible semantic
spec и structured wireframe с IDs; запусти validate_graph.py на этих файлах.
Containment canonical: relationship type=containment, from=container,to=child;
wireframe nodes[].parent отражает ту же принадлежность, entity.parent не используется.

Осмотри wireframe в target size: direction, containment, sequence, hierarchy,
label placement, essential path и grayscale. Standard допускает self-review G2.
Strict требует независимый G2; недоступность reviewer явно записывается, полезная
подготовка продолжается, strict release остаётся pending. Known structural blocker
исправляется перед стилизацией.

## 4. Выбери стиль и renderer

После структурной проверки используй
[style router](../references/style/style-router.yaml), один выбранный profile и
[renderer router](../references/production/renderer-router.yaml). Проверяй реальные
capabilities среды. Числовые routing thresholds — эвристики, не гарантии.

- topology/process/architecture → diagram-first;
- quantitative comparison → chart-first;
- object illustration with low topology risk → guided-generation;
- structured relations plus illustrative layers → hybrid-structured.

Standard записывает style/renderer/render instructions в brief; strict создаёт
render brief и необходимые style/output contracts. Содержание определяет число
этапов, колонок и панелей. Стиль не добавляет сущности, связи, цифры или шаги.

## 5. Создай editable source и export — G3

Сохрани source.svg, source.html, source.pptx или эквивалентный редактируемый
исходник. Для generated imagery помести raster layer в composite с редактируемыми
подписями и управляемой topology. Из одного PNG без редактируемой структуры
не получается полный create package.

Экспортируй требуемый final file. Сначала проверь применимые deterministic
свойства: dimensions/ratio, IDs, exact text, values/units, bounds/clipping и
critical edges. Затем осмотри фактический render: видимость, hierarchy,
псевдосвязи, подписи, target-size readability. Не заявляй визуальный PASS по
наличию SVG/XML. Недоступный визуальный осмотр записывается not_run.

Повторяй render ради наблюдаемого дефекта или невыполненного обязательного
требования; сохраняй уже достаточный удачный результат. Если exact ratio не был
hard requirement, сообщи фактический размер и не запускай косметический цикл
перегенерации из-за несущественного отклонения.

## 6. Понятность и выпуск — G4

Standard: ответь на подготовленные вопросы только по изображению, проследи
существенный путь, проверь читаемость в целевом размере, кодировки без опоры только
на цвет и пригодность export. Independent final critic — при доступности; иначе
честно отметь self-review и отсутствие независимой проверки.

Strict: независимый финальный critic, подробные questions/path tracing,
teach-back, применимые accessibility и regression checks. Human timed testing
не считается выполненным без реальных участников и записанного протокола.
Model first-impression — модельная проверка, не измеренные пять секунд.

G1/G2/G3 PASS означает advance_to_next_stage. Release PASS означает G4 PASS и
выполненные применимые prerequisites. Required not_run и known blocker не PASS.
При недоступном независимом strict critic выдай подготовленный candidate с
pending review; не утверждай strict release.

## Выход

Standard: brief с QA + visible wireframe + editable source + final export.
Strict добавляет schema-valid production/content/semantic/visual/render/QA
contracts, source/provenance и alt/long description. При stop_at выдай последний
реально созданный артефакт и соответствующие checks; не имитируй следующие стадии.
