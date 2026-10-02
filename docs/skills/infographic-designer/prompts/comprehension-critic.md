# Comprehension Critic

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

Вход: реальный visual artifact, intended viewing size и заранее заданные
comprehension questions. Это model first-impression review: он не измеряет
пять секунд восприятия и не заменяет тест с людьми.

1. По изображению назови тему, главный объект/маршрут и главный вывод.
2. Ответь на вопросы только по видимым элементам, без внешних знаний.
3. Проследи essential path и направление связей; strict — подробный path trace.
4. Для strict сформулируй teach-back одним предложением.
5. Укажи evidence region и confidence каждого ответа, неясные/догадочные ответы.
6. Осмотри читаемость в фактическом target size; назови проверенный размер.

Standard требует вопросы и target-size осмотр; strict добавляет полный protocol
и независимого финального critic. Если изображение/размер недоступны, запиши
not_run, не имитируй наблюдение. Reviewer kind self/independent/human фиксируется
по фактическому исполнителю. Human timing/participant claims требуют настоящего
теста и записанного evidence.
