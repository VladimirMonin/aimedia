---
applyTo: "**"
name: "DOCS.CommitMessages"
description: "Читай при подготовке Git commit: смысловая группировка изменений, Conventional Commits со scope, русские сообщения, explicit staging, verification evidence и запреты удалённых или деструктивных Git-операций."
---

# DOCS — Коммиты aimedia

## Границы применения

Правила развивают commit plan C00–C17 из `docs/plans/README.md`. Идентификаторы Cxx
описывают логические поставки плана, но не требуют подгонять фактическую историю под
ложно завершённый этап.

В Git-репозитории агент с разрешением на реализацию фиксирует каждый завершённый и
проверенный логический срез локальным commit, если пользователь не попросил оставить
изменения незакоммиченными. Не спрашивай заново «один или несколько»: группируй по
смыслу. Read-only задача не разрешает staging/commit. При делегировании staging и
commit выполняет основной интегратор, а не worker/reviewer.

## Формат сообщения

```text
<prefix>(<scope>): <Краткое название изменения по-русски>

- Завершённое изменение поведения или контракта
- Существенная защита, проверка или ограничение

Stage: E01
Requirements: R01, R02
Tests: tests/...::test_name
Verification: <реальные команды и результаты либо путь к evidence>
Migration: <изменение схемы/восстановление либо none>
```

Это шаблон, не требование заполнять несуществующие данные. `Stage`, `Requirements` и
`Tests` указывай только при действительной связи со стадией/матрицей; для
организационного docs-коммита не изобретай ID или тесты. `Verification` обязателен.
`Migration` обязателен при изменении схемы/данных; для значимого foundation/release
commit допустимо явное `Migration: none`.

Допустимые префиксы: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`,
`build`, `ci`, `perf`, `revert`. `revert` как тип сообщения не разрешает выполнять
откат. Scope отражает владельца: например `foundation`, `domain`, `provider`,
`registry`, `storage`, `artifacts`, `execution`, `cli`, `help`, `release`,
`instructions`.

Заголовок и тезисы пиши по-русски и разделяй пустой строкой. Обычно достаточно 2–5
тезисов. Для многострочного сообщения используй UTF-8 файл и
`git commit --file <file>` с настоящими переводами строк; временный файл сообщения
не включай в commit.

## Смысловая атомарность

Один commit — один проверяемый связный пользовательский результат, не одно поле
или файл. Он может охватывать несколько зависимых этапов: контракт, код, migration,
релевантные tests/docs и инструкции идут вместе, с одним consolidated gate/review.
Не смешивай независимые задачи, dependency updates и массовое переименование. Не включай чужую или незавершённую работу даже при общем пути файла.

По умолчанию коммить завершённый зелёный срез. Красный TDD commit допустим только в
явно согласованной рабочей ветке: ожидаемый failure обозначен, регрессия не скрыта,
commit не объявляется accepted checkpoint, release candidate или завершённым этапом.

## Preflight и staging

1. Проверь repo root, branch/worktree, `git status --short --branch` и исходный diff.
2. Прочитай затронутые инструкции и проверь scope/отсутствие чужих изменений.
3. Выполни применимые проверки; сохрани команду, cwd, raw exit code и пропуски.
4. Просмотри полный diff, включая новые файлы. Исключи secrets, `.env`, личные
   изображения, пользовательские prompts, БД, логи, caches, build outputs и
   временные artifacts.
5. Используй явные пути в `git add -- <paths>` или точные hunks. Не применяй
   без разбора `git add .`/`git add -A` в общем или грязном рабочем дереве.
6. Проверь `git diff --cached`, `git diff --cached --check` и staged file list.
7. После commit проверь его состав, точный SHA и оставшийся status. Не очищай дерево
   ценой удаления независимых изменений.

## Проверки до принятого commit

**Только документация или bootstrap без кода:** проверь YAML front matter инструкций,
внутренние Markdown-ссылки и пути, каталог `AGENTS.md`, отсутствие ссылок на чужой
проект, согласованность терминов и статусов с `docs/plans/README.md` и `01`–`09`.
При наличии Git обязательны `git diff --check`, просмотр staged diff и
`git diff --cached --check`. Не запускай отсутствующий build script и не записывай
его отсутствие как PASS.

**После bootstrap кода:** сначала проверь реальные `pyproject.toml`, `uv.lock` и
`scripts/`. Плановые базовые gates:

| Область | Проверка после появления инфраструктуры |
|---|---|
| Style | `uv run --locked --no-env-file ruff check .` |
| Format | `uv run --locked --no-env-file ruff format --check .` |
| Types | `uv run --locked --no-env-file mypy src/aimedia` |
| Unit/contracts | `uv run --locked --no-env-file pytest tests/unit tests/contracts -m "not live" --strict-markers` |
| Общий gate | `uv run --locked --no-env-file python scripts/quality.py quick` |

Команды являются целевыми до E01 и активными только после появления и проверки
соответствующей инфраструктуры. Для полного/release gate используй фактические
режимы `scripts/quality.py`, описанные в плане и реализованном script.

Обязательный failure блокирует принятый срез. Исправь ошибку или явно отдели
baseline blocker; не отключай test, coverage, network guard или правило ради зелёного
результата. Сохраняй **сырой exit code каждого процесса**: фильтр, `tail` или
успешный последний шаг pipeline не доказывает успех предыдущего. `NOT_RUN`,
`SKIPPED`, `XFAILED`, `NOT_COLLECTED` и `BLOCKED` не равны `PASSED`.

## Первый commit и документационный baseline

До первого commit:

- создай `.gitignore` до появления secrets/local data;
- убедись, что `.env`, `.pi/`, virtualenv, caches, DB и build artifacts не staged;
- зафиксируй существующие спецификации без попутного переписывания содержания;
- добавь `AGENTS.md` и базовые инструкции тем же foundation-срезом;
- проверь, что все локальные Markdown-ссылки разрешаются, front matter читается,
  а donor-specific имена/пути отсутствуют вне текста, где донор назван явно.

Рекомендуемый тип сообщения: `docs(foundation): ...`. Первый commit не объявляет E00
завершённым: он фиксирует исходный корпус документов и правила работы.

## Деструктивные и удалённые операции

Без отдельного явного разрешения запрещены `git revert`, `reset --hard/--mixed`,
`clean -fd`, откат файлов через `checkout --`/`restore`, переписывание истории,
`rebase` опубликованной ветки и `commit --amend`. Для baseline не прячь чужие
изменения в stash: используй чистый checkout/worktree. Не удаляй чужие ветки или
worktrees.

Push, force-push, remote/fork creation, удаление remote branches, tags/releases и
публикация не следуют из разрешения на локальный commit. Push выполняй только по
явному запросу пользователя. Force-push в `main`/`master` запрещён.

В итоговом отчёте сообщи SHA, состав commit, проверки и оставшиеся ограничения.
Если commit не создан, скажи это прямо и не выдумывай контрольную точку.
