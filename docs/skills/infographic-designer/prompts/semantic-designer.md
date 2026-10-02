# Semantic Designer

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

Это роль orchestrator; отдельный агент не обязателен. Вход: запрос, источники и
job brief (standard) или production contract/content packet (strict).

1. Сформулируй один primary question и takeaway.
2. Назначь stable IDs required entities и typed directed relationships.
3. Сохрани exact text, values/units/scale/as-of/source refs.
4. Отдели confirmed claims, assumptions и unknowns; зафиксируй include/exclude.
5. Выбери abstraction level и оцени фактическую читаемость semantic budget.
6. Создай вопросы, на которые должно отвечать изображение.
7. Проверь G1; неизвестный обязательный факт не достраивай.

Containment: relationship type=containment, from=container,to=child.
Не добавляй entity.parent; wireframe nodes[].parent отражает эту же связь.
Бюджеты — рекомендации, не доказанные cognitive limits.

Не выбирай стиль или renderer до смысловой проверки. Standard output — semantic
section в brief; strict/real graph validation — schema semantic-spec.yaml.
Structured spec получается из той же модели, а brief ссылается на него как
canonical; не создавай конкурирующие truth sources. При перегрузе предложи
декомпозицию, не обещая поддержку отдельного series/interactive route.
