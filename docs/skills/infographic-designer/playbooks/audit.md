# Audit — проверка существующей инфографики

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## Вход и rigor

Один orchestrator сравнивает существующий artifact с источником, требованиями,
target medium/size и применимыми rule IDs. Выбери standard по умолчанию или strict
по риску/запросу согласно [working modes](../docs/working-modes.md).
Аудит возвращает findings, а не создаёт новый visual и не исправляет его молча.

Источник и contracts желательны. Если contracts отсутствуют, создай краткий
observation packet с inferred/unknown; в standard не навязывай полный набор YAML.
Нет truth source — нет factual Semantic PASS, но наблюдаемые дефекты направления,
clipping, contrast или читаемости всё равно можно сообщить.

## Проходы

1. Semantic: claims/entities, неподтверждённые элементы, exact wording и values.
2. Structural: topology, direction, containment, order, hierarchy и reading path.
3. Visual/cognitive: группировка, density, label proximity, ответы на вопросы
   только по изображению и essential path.
4. Data: units, shared scale/baseline, category order, uncertainty и provenance.
5. Accessibility: contrast, redundant encoding и описание при необходимости.
6. Operational: actual dimensions, clipping, target-size readability и editability.

В standard объединяй связанные findings в короткий QA. Strict сохраняет schema
QA report и подробные evidence, questions, path tracing и regression, где применимо.
Используй independent critic при доступности в standard; strict final требует его.
Self-review не становится независимым от смены роли или промпта.

Model first-impression допустим как модельное наблюдение. Не заявляй измеренный
five-second test или human teach-back без настоящего теста. Недоступный visual
inspection фиксируется not_run; file checks не заменяют осмотр изображения.

## Evidence и verdict

Для каждого finding укажи rule_id, severity, expected, observed, evidence,
minimal_fix и confidence/unknowns. Evidence — конкретный регион artifact,
сопоставленная source claim или результат реально выполненной проверки.

- pass: применимые проверки выполнены и hard fails нет;
- repair: нужен локальный patch;
- rebuild: каркас/композиция требует перестройки;
- block: критическая неопределённость мешает подтвердить требуемый результат.

Отсутствующие факты и проверки явно ограничивают verdict. Strict independent
review unavailable оставляет final release pending; отчёт всё равно выпускается.
G1/G2/G3 PASS означает advance_to_next_stage; release разрешается только G4 PASS
с выполненными prerequisites. Score относится лишь к gate-passing вариантам.
Standard output — короткий Markdown QA; strict — schema-valid qa-report.yaml.
