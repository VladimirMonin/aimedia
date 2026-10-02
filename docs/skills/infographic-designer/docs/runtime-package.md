# runtime-package

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## Один источник и две формы пакета

**Development** содержит полную библиотеку навыка и является единственным
источником изменений. Curated contracts, templates, rules, prompts и playbooks
редактируются здесь. Curated документы версии 0.2.0 являются редакционной
производной и не обязаны буквально совпадать с извлечёнными фрагментами.

**Runtime** содержит только literal allowlist из `distributions.runtime.files`
в [manifest.yaml](../manifest.yaml). Installed runtime не редактируют вручную.
Version в manifest и SKILL совпадает; поле `distribution` явно различает формы.

Исторические evals и inbox-provenance остаются неизменяемыми материалами
разработки. Source map, snapshots, extractor и файлы с окончанием
`.generated.md` сохраняются в development. Эти материалы не входят в runtime.
Проверка provenance сравнивает сохранённые источники с SHA256 snapshot,
проверяет все selectors, canonical owners и точный текст selected extraction
targets из исторического baseline. Она не переписывает источники или Atlas.

## Проверка и воспроизводимый export

**С версии 0.2.1 в поставку входит screenshot-explainer:** канонические файлы находятся
в `modules/screenshot-explainer/`. Главный SKILL и routes направляют туда запросы
пояснить UI по скриншоту. Модуль включает предварительный план и декомпозицию;
общий infographic pipeline для этого сценария не требуется.
Для отдельного вызова можно скопировать эту папку под именем `screenshot-explainer`
в активный каталог skills. Такая копия, включая прежнюю соседнюю dev-папку,
синхронизируется из модуля; следующие правки вносят в канонический модуль внутри пака.

**Профиль выбирается явно.** Без `--distribution` validator использует поле
manifest; запрос другого профиля даёт ошибку. Missing required files,
malformed manifest и broken local Markdown links блокируют выпуск.

В обоих профилях проверяются каждый файл runtime allowlist, обещанные
contracts/templates, JSON schemas, valid template instances, route IDs,
registry/group/gate parity и regression coverage. Runtime дополнительно
проверяет точный состав файлов. Hash receipt является опциональным служебным
evidence и проверяется только при явном флаге `--verify-hashes`.
Development дополнительно проверяет extraction baseline и immutable provenance.
Python `__pycache__` является производным кешем исполнения; он не считается
файлом пакета и никогда не экспортируется.

Из корня development:

```powershell
python scripts/validate_package.py . --distribution development
python scripts/build_runtime.py . ../infographic-designer-runtime-0.2.0-candidate
python ../infographic-designer-runtime-0.2.0-candidate/scripts/validate_package.py ../infographic-designer-runtime-0.2.0-candidate --distribution runtime
python ../infographic-designer-runtime-0.2.0-candidate/scripts/run_smoke_tests.py ../infographic-designer-runtime-0.2.0-candidate
python tests/test_runtime_package.py
```

Builder принимает только новый staging path вне development root. Он не
удаляет, не заменяет и не устанавливает существующие каталоги. При ошибке
после создания staging candidate остаётся доступным для разбора.
Единственное преобразование export меняет manifest distribution на runtime.

По умолчанию builder не создаёт receipt, а validator его не требует.
При необходимости добавьте `--verify-hashes` к build и validate.
**Опциональный receipt `runtime-hashes.json`** содержит относительные POSIX paths и SHA256
каждого allowlisted файла, включая преобразованный manifest. Собственный
hash receipt не включается. В receipt нет timestamp, абсолютного source path,
секретов или provenance. Это служебная сверка export, не gate обычной работы.

## Review, установка и rollback

Перед установкой проверьте candidate, его smoke result, отсутствие historical
materials. Structural checks
не доказывают качество конкретного визуального результата или живое
срабатывание агента.

Сохраните предыдущую installed копию отдельно.
Установка производится только после принятия candidate ответственным агентом.
Не объединяйте новый allowlist поверх старого дерева: это сохранит устаревшие
generated и provenance файлы. Для clean replacement сначала проверьте точный
installed target и backup, затем выполните принятую замену. Сам builder её
не выполняет.

Rollback возвращает сохранённую предыдущую installed копию целиком. Сверьте
состав backup и restored копии до дальнейшей работы. Историческая
версия может не проходить новые gates версии 0.2.0; сохранение её байтов и
восстановимость проверяются отдельно от acceptance нового runtime.
