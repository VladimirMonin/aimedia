# source-map

**Приоритет:** исходный план разработки 1.0 → актуальное уточнение пользователя →
вторичная учебная сессия. Имена и хеши исходных двух файлов записаны в development
рядом с пакетом; их частный текст не включён в распространяемый навык.

Формат точки входа сверялся с [Agent Skills specification](https://agentskills.io/specification):
name/description, совпадение имени каталога, компактная точка входа, относительные
пути и условные руководства. Ссылка на спецификацию — основание формата, не
runtime-зависимость и не гарантия работы во всех клиентах.

| Снимок | Репозиторий на зафиксированной ревизии |
| --- | --- |
| shuohao-skills | [ef4ac0c](https://github.com/eternityspring/shuohao-skills/tree/ef4ac0c313c7eeb1f918db5f0f0eb319745900bc) |
| baoyu-skills | [1567581](https://github.com/JimLiu/baoyu-skills/tree/1567581c26ec29f4216c6e6835415bf30343b0e3) |
| storyboard-skill | [a8908aa](https://github.com/toyme/storyboard-skill/tree/a8908aabcf9e9585220dfda4d7656f95f68b3610) |
| Storyboard | [76a78c5](https://github.com/Zhekinmaksim/Storyboard/tree/76a78c55922d1b007b1478aafb6faaf672487e53) |
| Аудионавык | [7fd796f](https://github.com/yaochang007/remotion-video-builder-with-qwen-audio/tree/7fd796f00fc5b6b02aaa6c593d3e62547bd0e5a4) |

[upstream-lock](../provenance/upstream-lock.json) содержит repository URL, commit SHA,
commit date, путь, Git blob SHA, SHA-256 и прямую ссылку на каждый скачанный файл.
[Карта адаптаций](../provenance/adaptation-map.md) перечисляет изученные участки и
изменения. Лицензии проверены по файлам выбранных ревизий, а не по поисковому кешу.
Скачаны только текстовые части выбранных skills, вспомогательные scripts для
чтения и уведомления. Сторонний runtime не запускался; бинарные ассеты не перенесены.

anything2explainer записан excluded по разделу 13.7 плана. Его ограниченные
материалы не используются. Собственные примеры, пресеты и валидаторы написаны
для этого пакета; исторические тезисы учебной сессии не стали фактами эталонов.
