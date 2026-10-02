# handoff-contract

**Схема 1.0 — текстовый документ, не база данных.** В JSON обязательны kind,
schema_version, project_id, episode_id, document_revision. Редакция — строка.
ID в kebab-case устойчивы при перестановке и правке; order — отдельный полный ряд.
Персонажи и панели в своей коллекции уникальны; panel.id уникален во всём выпуске.
JSON является источником связанных строк, Markdown — его представлением.

| Документ | Формальная схема |
| --- | --- |
| preproduction-package | [preproduction-package](../schemas/preproduction-package.schema.json) |
| project-context | [project-context](../schemas/project-context.schema.json) |
| review | [review](../schemas/review.schema.json) |

`scope=full` требует brief, story, style, characters, references, source_claims,
beats, lines, variants, pages, image_prompts, voices, shots, voice_scripts, reviews.
Пустые characters допустимы для истории без героев; beats, variants, pages,
image_prompts и voice_scripts не пусты. `scope=partial` позволяет только нужные
коллекции, но каждая включённая ссылка должна разрешаться в том же пакете.
У короткого prompt-only ответа эти данные остаются в контексте, не печатаются.
`missing_inputs` перечисляет требуемые материалы; ready с ним несовместим.

Проект по умолчанию наследуется от корня. Необязательное project_id записи должно
совпадать. origin_project_id требует явно approved cross_project_permissions.
ProjectContext не смешивает проекты, содержит context_revision и отдельные
episode_summaries/open_threads. ContextDelta сравнивает base_revision и old_value;
его сохранение и применение выполняются снаружи.

StoryBeat имеет purpose, visible_event, before/after, source_claim_ids,
interpretation и для metaphor — metaphor_limit. Character содержит traits с origin
и approval вместо обязательного человеческого набора. Style.prompt_block — краткое
описание для переносимого промпта. Voice — творческий профиль speaker_id.

Page принадлежит variant_id, содержит declared_panel_count, panels и
allowed_overlaps с причинами. Panel.rect в координатах страницы; text_zones.rect
в координатах панели. Line связывает canonical_text/on_image_text/spoken_text,
delivery и явно approved text_variants для отличий. narration говорит narrator,
dialogue — персонаж, label — none. Label не включается в речь автоматически.

ImagePrompt связывает target_type/target_id с одной единицей, panel_ids и
visible_lines с её постановкой. declared_panel_count сверяется с целевой единицей.
ReferenceBinding описывает доступ, просмотр, утверждение и роль. available требует
locator и availability_evidence host; inspected дополнительно inspection_evidence.
Валидатор проверяет декларации, не открывает locator и не доказывает наличие байтов.

VoiceScript содержит mode, selected_line_ids и хронологические events. Events
ссылаются на Line и Voice; текст не копируется в независимый TTS-список. Shot
связывает visual_ids с одним variant. Связи многие-ко-многим допустимы.
pause не имеет line_id/voice_profile_id. Повтор речи требует repeat и reason.
Timing: qualitative note; estimated range_seconds/note; externally_supplied
start/end/source. Последний вариант — внешние сведения, не измерение навыком.

Готовность (`draft`, `ready`, `needs_input`, `blocked`), согласование и результат
review независимы. Ready не означает approved или произведённое медиа.
Failed/skipped с пустыми findings не считаются passed.

Проверка использует полную библиотечную JSON Schema Draft 2020-12 с зафиксированной
зависимостью [requirements-checks](../requirements-checks.txt), затем собственные
семантические правила. Коды: 0 — passed, 1 — дефект входа, 2 — сбой выполнения.
JSON stdout — один документ. Скрипт read-only, без сети, медиа и исправлений.
Присутствие строк в prompt text не доказывает роль героя или художественное качество.
