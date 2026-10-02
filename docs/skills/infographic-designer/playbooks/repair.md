# Repair — минимальное исправление без побочных изменений

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## 1. Зафиксируй scope и rigor

Выбери standard/strict по [working modes](../docs/working-modes.md). Сохрани
before artifact, исходник и findings. Preserve перечисляет неизменяемые свойства;
modify содержит конкретные target IDs/properties, operation и expected result.

Standard хранит scope в brief. Strict либо реальный machine preserve validator
используют schema edit contract. Из явного запроса «поменяй только стрелку X»
можно сразу вывести точный scope; не запрашивай повторную approval обычного
авторизованного patch. Неизвестный критический target/source нужно разрешить
до зависимого изменения.

## 2. Составь минимальный patch

Приоритет: semantic → structural → comprehension → text/data → visual → style.
Для каждого изменения укажи target ID, operation, expected result, preserved
properties и regression checks. Не меняй элементы вне modify scope.

Предпочитай editable SVG/HTML/PPTX или structured graph. Raster edit допустим,
когда можно наблюдать границу изменения; если её нельзя доказать, сообщи
ограничение/rebuild recommendation, сохраняя before. Не заменяй renderer или
композицию ради стилистического улучшения, если этого нет в scope.

## 3. Примени и проверь

Structured before/after + edit contract → validate_preserve_contract.py.
При image comparison осмотри unchanged objects, wording, proportions, layout,
reading order и regions вне modify. Machine preserve check не доказывает
визуальное сохранение сам по себе.

Перезапусти failed gate, затронутые prerequisites, scope regression и G4.
Standard: короткие before/after evidence, essential path и target-size review.
Strict: structured evidence и независимый G2, если topology затронута, плюс
независимый final. Reviewer unavailable записывается честно; продолжай preparation
и self-review, но strict release PASS остаётся pending.

G1/G2/G3 PASS разрешает следующий этап; release только G4 PASS с prerequisites.
Известный semantic/structural blocker должен быть исправлен, а не компенсирован
style polish. Не объявляй human/timed testing выполненным по model review.

## Выход

Верни исправленный editable source и export, короткий QA (standard) либо
schema QA report (strict). Перечисли fixed findings, remaining unknowns, scope
comparison evidence и проверки not_run. Out-of-scope изменение — REG-003 blocker.
Если исправление не доказано, не описывай его как принятую repair.
