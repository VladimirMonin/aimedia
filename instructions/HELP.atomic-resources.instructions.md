---
applyTo: "src/aimedia/help/**,src/aimedia/cli/commands.py,tests/cli/**"
name: "HELP.AtomicResources"
description: "Читай при изменении packaged Markdown help/topics, front matter, related links и dynamic Registry directives: строгий allowlist без eval, resolved raw/JSON equivalence и installed resources вне cwd."
---

# HELP — Atomic package resources

Владелец — `help.registry.HelpRegistry` и `help/data/*.md`; источник —
[09](../docs/plans/09-documentation-help.md), baseline D12.

- Resources читаются через importlib.resources, не ./docs/cwd. Topic metadata
  data-only YAML: topic/title/summary/status обязательны, extra поля запрещены;
  duplicate topics и broken related topics — ошибка. Runtime docs описывают
  только реализованные команды, не планы 01–09.
- Разрешены только `{{models}}` и `{{model:<canonical-id>}}`. Никаких eval,
  constructors, include/template engine. Модельный блок строится из того же
  EffectiveModelDefinition/view, что validation и models show; второй таблицы
  enum/default/лимитов/цен в Markdown нет.
- `help <topic> --raw` — resolved Markdown без front matter/неразрешённых
  directives/ANSI; `--json` содержит точно тот же markdown и metadata/related.
  Human rendering — Rich Markdown, не выполнение HTML/JS.
- Каталог documented/experimental явно live unverified. Опубликованная pricing
  metadata не является actual billing или будущей гарантией.
- Help не пишет пользовательские данные и не требует credentials/API. Тесты
  resources/first-run примеров выполняются offline в tmp roots; installed wheel
  проверяется вне checkout/cwd, включая console entrypoint.

Применимые проверки: tests/cli/test_complete_cli.py и installed package smoke;
один consolidated quality/build/docs check вместе с функциональной поставкой.
