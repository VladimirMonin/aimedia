# adaptation-map

**Дата адаптации: 2026-10-02.** Все инструкции ниже написаны заново и изменены
относительно назначения доноров. Точные ревизии и хеши всех скачанных файлов
содержит [upstream-lock](upstream-lock.json). Это пины репозиториев и Git blob SHA,
а не смешанный снимок поискового кеша. Оригиналы находятся вне установочного пакета.

| Донор и просмотренный материал | Адаптированные разделы | Изменение |
| --- | --- | --- |
| baoyu-comic: SKILL, analysis-framework, storyboard-template, webtoon, cinematic | story-development, source-handling, storyboard-layout, format-adaptation, prompt-writing | Страница с панелями; анализ по задаче; нет генерации, обложки по умолчанию, EXTEND и повторов |
| shuohao: novel-outline/SKILL, novel-script/SKILL, novel-characters/schema | story-development, source-handling, characters-and-references, voice-script | План отдельно от постановки; речь отдельно от действий; нет китайских норм, сериализации и обязательной человеческой внешности |
| shuohao: character-refs/SKILL, novel-art/SKILL, novel-storyboard/SKILL и shot-writing | characters-and-references, locations-and-props, visual-storytelling | Источник признаков; необходимые виды; identity отдельно от позы; без генераторов, белого фона и протоколов video API |
| shuohao: novel-storyboard/scripts/selftest.mjs, фрагменты coverage/dialogue | test_contract, отрицательные фикстуры | Собственные проверки пропуска, повторов и порядка; намеренная связь многие-ко-многим разрешена |
| toyme: SKILL, project-structure, prompt-patterns | project-context, revision-and-review, prompt-writing | Минимальный контекст и preserve/change; нет обязательного файлового дерева и каталога промптов |
| Storyboard: SKILL, critique-criteria, critique.py/iterate.py, участки проверок | revision-and-review, quality-rubric, validate_contract | Конкретное замечание и базовая редакция; failed/skipped отличаются от passed; нет Kimi, рендера SVG и киношных абсолютов |
| Аудиодонор: SKILL, tts-manifest-template, voice-casting, layout-rules, qa-checklist | voice-script, storyboard-layout, quality-rubric | Речь по репликам и творческие голоса; четыре режима; без Qwen/Remotion и provider/file обязательств |

Наблюдение Storyboard на pinned-версии: critique.py возвращает пустой список при
KimiError и ошибке JSON. Это изучено статически; его API не запускался. Наш review
имеет явный failure_reason. Скрипты и тесты доноров не исполнялись; утверждения
их README о количестве тестов не выдаются за собственную проверку.

Большая часть скачанного снимка сохранена для повторной сверки. Наличие файла
в lock не означает, что он полностью использован или перенесён. Из контекста
исключены anything2explainer, runtime-адаптеры, долгосрочное хранение и чужие медиа.
