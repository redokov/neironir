# Code review: neurodoc (агент-оркестрация)

**Метод:** 5 доменных ревьюеров (erpband/anthropic/claude-opus-5.5), минимальный скоуп на агента, structured output.
Adversarial-верификация high-находок: 9/9 подтверждено, 0 опровергнуто.
Всего находок: 31 (high: 9, medium/low: 22).

## Подтверждённые HIGH (verifier: isReal=true)

### [HIGH] backend/neironir/converters/docx.py:225 — Cross-boundary replacements leave part of the detected text unredacted

When a replacement spans two paragraphs or table cells, the code deliberately clips it to the first element (lines 267-275) and leaves the remaining elements unchanged. For extracted text `John\nDoe` with a span covering both words, only `John` is replaced and `Doe` remains in the output. This contradicts the documented contract that such spans should be rejected and can leak detected data.

**Fix:** Validate replacement boundaries and raise a clear ValueError, or apply the replacement across all covered elements while preserving the separator semantics; do not silently leave the tail untouched.

### [HIGH] backend/neironir/converters/docx.py:321 — Paragraph hyperlink content is deleted during every build

`build()` rewrites every paragraph. `_replace_text` clears normal runs, removes all `w:hyperlink` elements, and only writes the new text if `paragraph.runs` is non-empty (lines 325-336). A paragraph containing only a hyperlink has no normal runs, so its visible label is deleted even when there are no replacements; a paragraph mixing normal text and a hyperlink also loses the hyperlink text.

**Fix:** Extract hyperlink runs as part of the paragraph text and recreate the resulting text in a run, or preserve hyperlink XML when the paragraph is not being changed. Ensure a paragraph with no ordinary runs still receives the rewritten text.

### [HIGH] backend/neironir/converters/docx.py:106 — Nested-table content is omitted from extraction and therefore cannot be redacted

The element map only walks top-level `w:tbl` children and iterates their direct row cells (lines 106-123). `_element_texts` then uses `cell.text` for those cells (lines 165-179), which does not include paragraphs/tables nested inside a cell. PII in a nested table is absent from `extract_text`, so the pipeline never creates a replacement for it and the content remains in the generated DOCX.

**Fix:** Recursively walk block-level children in table cells, including nested tables, and use the same recursive element map for extraction and rewrite projection; alternatively reject nested tables explicitly before producing an apparently sanitized document.

### [HIGH] backend/neironir/api/jobs.py:356 — Feedback можно сохранить для незавершённого или несуществующего job

submit_feedback проверяет только наличие каталога через _ensure_job_dir_exists и не загружает job/status. Поэтому запрос можно принять для pending/processing/failed job, а каталог, созданный без корректного job.json, также считается достаточным. Это создаёт невалидное состояние feedback.json, которое затем будет учитываться статистикой и попадёт в обучение.

**Fix:** Загрузить job через storage.load_job и разрешать feedback только для COMPLETED; отдельно обрабатывать FileNotFoundError/битое job.json.

### [HIGH] backend/neironir/workers/feedback_applier.py:241 — Повторный apply-feedback разрушает offsets после первого применения

Каждый вызов читает уже изменённый result.md, но выстраивает alignment по исходным annotations.json (строки 241–244). После первого apply результат уже содержит пользовательские placeholders/reject-текст, поэтому позиции и набор placeholders больше не соответствуют исходной разметке; повторная отправка тех же или новых действий может заменить неверный фрагмент или использовать несуществующее соответствие. Endpoint заявляет идемпотентность, но реализация её не обеспечивает.

**Fix:** Хранить исходный результат/версию feedback и применять изменения от исходного текста один раз, либо сохранять состояние/версию и отклонять повторный apply без повторной записи.

### [HIGH] backend/neironir/workers/feedback_applier.py:322 — Перекрывающиеся feedback actions применяются с некорректными координатами

Все reject и add replacements вычисляются относительно одного cleaned_text и затем применяются справа налево без проверки пересечений. Если add-диапазон пересекает обнаруженный placeholder, либо несколько add actions перекрываются, последующая замена использует уже изменённую строку с прежними end offsets и может удалить/обрезать чужой replacement или оставить повреждённый текст.

**Fix:** Валидировать действия на непересечение и принадлежность диапазонам, а при пересечении отклонять конфликтующие действия до записи; либо строить единый результат по исходному тексту.

### [HIGH] frontend/login.js:35 — Непроверенный next позволяет сделать open redirect

Значение `next` целиком берётся из query string, затем после успешного ответа устанавливается в `window.location.href` (строка 44). Поэтому ссылка вида `/login?next=https://attacker.example` или `next=javascript:...` может перенаправить пользователя на внешний ресурс после входа.

**Fix:** Разрешать только локальные пути (например, начинающиеся с `/` и не с `//`) и отклонять схемы/внешние origin перед отправкой и перед присваиванием `window.location.href`.

### [HIGH] frontend/app.js:245 — Гонка загрузок может привязать интерфейс к неправильной задаче

POST загрузки не связан с `abortController` и не имеет проверки актуальности запроса. При быстрых последовательных выборах файлов ответы могут прийти в обратном порядке; строка 267 тогда перезапишет `currentJobId` старым `job.id`, после чего polling и скачивание будут работать для другого файла.

**Fix:** Хранить идентификатор/счётчик загрузочного запроса и после `await fetch` и `await res.json()` обновлять UI только если это всё ещё последняя загрузка; либо отменять предыдущий upload через отдельный AbortController.

### [HIGH] frontend/app.js:398 — Гонка открытия review может отправить правки старой задачи в новую

После запуска GET `/annotations` нет проверки, что `currentJobId` и открытая review-сессия остались прежними. Если пользователь нажмёт «Загрузить ещё» или выберет новый файл, а старый запрос завершится позже, строки 400–402 запишут старые `reviewData` и покажут их для новой задачи; последующий apply использует уже текущий `currentJobId`, то есть может отправить старые действия в новую задачу.

**Fix:** Сохранять `myJobId`/токен открытия перед fetch и после ответа сравнивать его с текущим job/session token; при несовпадении игнорировать результат.

## Medium / Low (verifier не запускался — экономия токенов)

- **[MEDIUM] backend/neironir/converters/docx.py:237 — Zero-length replacements are not supported despite being explicitly allowed** — The code claims to allow insertion replacements at lines 262-263, but it computes `end_idx` from `replacement.end - 1` at line 238. For `start == end == 0`, this calls `_find_element_index(..., -1)` and raises `ValueError`; at the start of a later element, `end_idx` points to the preceding element and the cross-boundary branch can replace the entire later element instead of inserting.
- **[MEDIUM] backend/neironir/converters/docx.py:133 — Merged table cells are indexed and rewritten multiple times** — The map creates one element for every `row.cells` entry (lines 133-148), but python-docx represents a horizontally merged cell by returning aliases to the same underlying cell. The extracted text can therefore duplicate one cell, and `_apply_rewrites` subsequently calls `_set_cell_text` on that same cell for each alias (lines 297-302). A rewrite stored for one alias can be overwritten by the uncha
- **[MEDIUM] backend/neironir/converters/docx_to_md.py:101 — Ragged DOCX table rows silently lose cells in Markdown** — Column widths are computed with `zip(*rows)`, and both header and body rendering also use `zip(row, widths)` (lines 101-106). If any row has fewer cells than another, `zip` truncates to the shortest row, so cells beyond that width are omitted from the Markdown output. Such row shapes can occur with non-uniform table XML/grid spans.
- **[MEDIUM] backend/neironir/converters/docx_to_md.py:290 — Structured-document content controls are dropped entirely** — The body walker yields output only for direct `w:p` and `w:tbl` children (lines 290-298). A `w:sdt` content control commonly wraps paragraphs or tables, but it is ignored along with all of its visible content. The converter consequently loses user text, and any sensitive text inside the control cannot be annotated or converted.
- **[MEDIUM] backend/neironir/workers/feedback_applier.py:114 — Alignment неверно сопоставляет одинаковые entity types при пропущенной/несогласованной аннотации** — _build_alignment (строки 126–149) ищет следующий placeholder только по entity_type и пропускает все placeholders других типов. При любой отсутствующей, дублированной или несортированной annotation последующая annotation того же типа может быть привязана к чужому placeholder, а reject затем восстановит неправильный текст через ann_to_alignment.
- **[MEDIUM] backend/neironir/api/schemas.py:73 — FeedbackItemIn не валидирует action и диапазоны** — Поля action, start, end, entity_type и text принимают любые значения: например action='add' с end<=start, отрицательными offsets или неизвестным entity_type проходит API и сохраняется как feedback. Часть таких данных молча пропускается applier-ом, но reject с некорректным original_span_index может вызвать ValueError и привести к ошибке запроса после записи feedback.json.
- **[MEDIUM] backend/neironir/admin/training.py:279 — Некорректный тип в ADD action роняет весь apply-feedback** — append_job_feedback_to_dataset вызывает int(action.get(...)) без обработки ValueError/TypeError. Хотя FeedbackItemIn сейчас принимает произвольные значения, malformed action может быть сохранён, после чего apply-feedback уже записал feedback.json и успешно применил rewrite, но падает при добавлении training dataset. Клиент получает 500, а состояние job/result и dataset расходятся.
- **[MEDIUM] backend/neironir/admin/training.py:191 — Конкурирующие training start перезаписывают общий временный dataset** — start_training_from_feedback формирует файлы output_dir/combined_dataset.jsonl и output_dir/.combined_dataset.jsonl.tmp без межзапросного lock. Хотя start_training позже защищает только момент запуска subprocess, два вызова параллельно могут оба пройти сбор dataset, перезаписать общий tmp через os.replace и удалить feedback_dataset.jsonl друг друга, после чего один из запусков может стартовать на 
- **[MEDIUM] backend/neironir/admin/training.py:494 — После timeout дочерний процесс может остаться живым и читать pipe** — На timeout вызывается proc.terminate(), затем сразу await proc.wait(). Если процесс не завершился на SIGTERM, wait может зависнуть без дальнейшего kill; кроме того asyncio.wait_for отменяет gather readers, а код не закрывает/дренирует stdout/stderr. Это оставляет subprocess/pipe resources и мониторинг в неопределённом состоянии, особенно если opf игнорирует SIGTERM.
- **[MEDIUM] backend/neironir/api/auth.py:120 — Login CSRF проверка Origin/Referer допускает null и некорректные значения** — `_origin_matches()` пропускает любой присутствующий заголовок, если `urlparse()` возвращает пустой `netloc`; в частности, реальный браузерный `Origin: null` и относительные/некорректные значения доходят до успешного входа. При отправке формы из sandboxed cross-origin контекста это позволяет навязать жертве сессию, созданную с учётными данными атакующего (login CSRF/session swapping). Кроме того, о
- **[MEDIUM] backend/neironir/storage/local.py:100 — save_result допускает path traversal через ext** — Параметр `ext` не валидируется, а подставляется в имя файла как `result.{ext}`. Значение вроде `../../outside` формирует путь за пределами `root/jobs/{job_id}` и позволяет перезаписать произвольный файл, доступный процессу, если значение попадёт сюда из недоверенных данных.
- **[MEDIUM] frontend/app.js:316 — Кнопка применения правок показывается для DOCX-результата, который API не поддерживает** — После любого completed job кнопка review показывается без проверки `ext` (строки 311–316). При отключённом для DOCX преобразовании результат имеет `output_ext=docx`, а вызов apply-feedback из строки 742 получает от backend jobs.py HTTP 400 (`docx_output_not_supported`). Пользователь может открыть проверку, но обещанное «Сохранить правки в файл» гарантированно завершается ошибкой.
- **[MEDIUM] frontend/app.js:665 — Локальный placeholder для private_organization получает неверный тип** — Панель выбора сущности предлагает `private_organization` (строка 561), но `typeToPrefix` не содержит этот ключ. Поэтому `nextPlaceholderForType` подставляет fallback `UNKNOWN` и после применения добавления локальный preview показывает `<UNKNOWN1>` вместо placeholder организации, рассинхронизируя UI с фактически отправленным `entity_type`.
- **[MEDIUM] privacy-filter/opf/_core/runtime.py:283-286; privacy-filter/opf/_core/sequence_labeling.py:145-159; privacy-filter/opf/_model/model.py:431-460:283 — Длинный текст режется без перекрытия контекстных окон** — `predict_text` строит окна через `example_to_windows`, а тот использует `stride = window_size`, поэтому соседние окна не перекрываются. При этом `sdpa` разрешает модели видеть только позиции внутри текущего окна и локальной bidirectional-полосы. Для каждого перехода между окнами токены теряют левый/правый контекст, а `ExampleAggregation` не может восстановить эти отсутствующие оценки; предсказания
- **[MEDIUM] privacy-filter/opf/_core/spans.py:36-41,74-79; privacy-filter/opf/_core/spans.py:232-236:232 — Субтокены, разрезающие UTF-8 символ, получают один и тот же диапазон** — Диапазоны вычисляются по границам Unicode-символов, хотя один BPE-токен может содержать только часть UTF-8 последовательности. Например, `cl100k_base` кодирует `🙂` двумя токенами с байтами `f0 9f` и `99 82`; формулы с `bisect_right`/`bisect_left` назначают обоим токенам диапазон `[0, 1)`. Поэтому если модель присвоит этим двум токенам разные BIES-метки, span только второго токена в `token_spans_to
- **[LOW] backend/neironir/converters/docx_to_md.py:126 — Localized numeric heading styles are never recognized** — The second regex alternative matches styles such as `1 уровень`, `2 heading`, and `1 заголовок`, but it does not capture the number (line 126). `_heading_level` always reads capture group 1 at line 145, which is `None` for that alternative, so it returns `None` and emits the heading as a normal paragraph.
- **[LOW] backend/neironir/converters/docx_to_md.py:97 — Unescaped table delimiters and line breaks produce invalid Markdown tables** — Table cells are joined directly with `" | "` and rendered without escaping (lines 97-108). A cell containing `|` creates extra Markdown columns, and a cell containing a newline creates additional physical table rows. The converted Markdown is then parsed with a different structure from the DOCX, which can make cell data inaccessible or misassociated.
- **[LOW] backend/neironir/api/jobs.py:253 — COMPLETED job без result-файла приводит к необработанному 500** — download проверяет только статус job, затем вызывает read_bytes или создаёт FileResponse для result_path без проверки существования и без обработки FileNotFoundError. При сбое/ручном удалении файла persisted state остаётся completed, а API возвращает внутренний 500 вместо контролируемого 404/409.
- **[LOW] backend/neironir/auth/api_key.py:34 — Проверка API-ключа не является constant-time для набора ключей** — Хотя отдельные сравнения используют `secrets.compare_digest`, выражение `any(...)` прекращает перебор сразу после совпадения. Время обработки зависит от позиции совпавшего ключа в итерации `frozenset`; это противоречит заявлению функции о сравнении всех ключей и оставляет timing side channel для membership check.
- **[LOW] frontend/app.js:561 — Нельзя добавить пропущенную URL-сущность через review UI** — `ENTITY_LABELS` поддерживает `private_url` (строка 16), но массив кнопок в selection toolbar не содержит `private_url`. Пользователь может выделить пропущенный URL, однако не получает способа отправить соответствующий `add` action, хотя тип поддерживается API/режимом обнаружения.
- **[LOW] frontend/admin.js:377 — Training polling никогда не освобождает interval** — `pollTrainingStatus` создаёт `setInterval` каждые 1.5 секунды и сохраняет его в `trainingPollTimer`, но ни при завершении обучения, ни при уходе со страницы interval не очищается. В результате открытая админская вкладка продолжает бесконечно запрашивать `/api/v1/admin/training/status`, даже когда training давно не запущен.
- **[LOW] privacy-filter/opf/_model/model.py:431-474,482-499:482 — Ветка bidirectional_context в полном attention-пути недостижима** — Условие на строке 431 уже направляет любой вызов с `bidirectional_context=True` в первый local-window путь. Поэтому `if bidirectional_context` на строке 482 и построение полной матрицы маски на строках 490-499 никогда не выполняются. Это dead code: изменения или исправления этого purported full-attention пути не влияют на inference и создают ложное впечатление, что этот режим поддерживается.

## Кросс-ревью (deepseek-ai/deepseek-v4.1-flash-1017, 31 независимый верификатор)

**Метод:** каждое из 31 замечания проверял отдельный агент с минимальным контекстом
(±60 строк вокруг места, effort=low, structured verdict). Сверка с вердиктами opus-5.5.

**Итог:**
- 29/31 подтверждено, 2 опровергнуто, 0 агентов без результата
- Согласие с opus по 9 high-находкам: **9/9** — все критичные дефекты реальны
- 20 medium/low, не проверявшихся opus, теперь подтверждены deepseek

### Опровергнутые (исключить из плана исправлений)

1. ~~[MEDIUM] admin/training.py:279 — int() в ADD action роняет apply-feedback~~
   Ложное срабатывание: schemas.py:70-78 объявляет start/end как strict int — Pydantic
   отклоняет malformed значения (422) до входа в эндпоинт; в feedback.json попадают
   гарантированно числа, ValueError на этом пути невозможен.
2. ~~[MEDIUM] admin/training.py:191 — конкурирующие training start перезаписывают общий tmp dataset~~
   Ложное срабатывание: combined/tmp/feedback-файлы пишутся в output_dir, который
   production-вызыватель (router.py:180) делает уникальным на запрос
   (_now_iso с микросекундами, router.py:353-359); межзапросной коллизии нет.

### Понижение severity (дефект реален, но влияние меньше заявленного)

| # | Находка | Комментарий deepseek |
|---|---------|----------------------|
| 21 | [HIGH] login.js:35 open redirect | подтверждён, но redirect:"manual" делает ответ opaque — практическое влияние ограничено; предложить high->medium |
| 13 | [MED] schemas.py:73 FeedbackItemIn | валидации нет, но боевой ущерб ограничен |
| 18 | [MED] auth.py:120 Origin:null | подтверждён (auth.py:129 guard не срабатывает), тянет на low-medium |
| 19 | [MED] storage/local.py:100 path traversal | код уязвим, но в production ext не приходит из недоверенных данных |
| 20 | [LOW] api_key.py:34 constant-time | низкий практический риск |
| 29 | [MED] spans.py:232 субтокены UTF-8 | подтверждён, редкий кейс |

### Итоговый приоритет исправлений (консенсус двух моделей)

1. **converters/docx.py** — три HIGH-утечки PII (cross-boundary #0, hyperlink #1, nested-table #2):
   подтверждены обеими моделями независимо, с уликами (clipped_count не инкрементируется,
   guard в _set_cell_text есть, в _replace_text — нет).
2. **workers/feedback_applier.py** — #10, #11 (offsets после повторного apply, перекрывающиеся actions).
3. **api/jobs.py:356** — feedback для незавершённого job.
4. **frontend/app.js** — #22, #23 (две гонки currentJobId).
5. Далее — подтверждённые medium (validation #13, CSRF #18, training #16) и low.
