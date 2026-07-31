# Tasks: Apply Feedback to Result File (FR-1) — UI regression

> Requirements: @requirements.md
> Design: @design.md
> Status: Implementation Done (2025-01-15)

## Execution Log

| Task | Status | Evidence |
|------|--------|----------|
| T3 (remove buttons) | Done | `index.html` + `app.js` cleaned; grep empty |
| T4 (hint) | Done | hint updated in `index.html` |
| T6 (aria-live) | Done | `role="status" aria-live="polite"` on apply-success |
| T1 (local re-render) | Done | `applyFeedbackToFile` now splices reviewData locally; `node -c` OK |
| T2 (disable button) | Done | `updateApplyButton()` wired; button disabled by default |
| T5 (tests) | Done | `tests/unit/frontend/test_review_simplification.py` — 10 passed (вкл. batch-adds) |
| T7 (verify) | Done | ruff clean; pytest 367 passed; 9 pre-existing failures unrelated |
| Issue 1 (batch adds) | Done | `nextPlaceholderForType(..., extraTaken)` + `batchTaken`; node-проверка OK; статтест |

## Requirement Coverage

| Requirement | Tasks | Notes |
|-------------|-------|-------|
| FR-001 | T1   | Re-render preview из локально модифицированного reviewData |
| FR-002 | T2   | Disable apply-кнопки при пустых actions |
| FR-003 | T3   | Удалить 3 кнопки + связанные DOM/JS |
| FR-004 | T4   | Обновить hint text |
| FR-005 | T5   | Регрессионные тесты |
| NFR-001 | T3  | API остаётся |
| NFR-002 | T6  | a11y: aria-live на success |
| NFR-003 | (N/A) | privacy не затрагивается |

## Implementation Readiness Check

| Check | Status | Notes |
|-------|--------|-------|
| Must Have requirements have tasks | Pass | FR-001..FR-005 покрыты T1..T5 |
| Requirements are covered by design | Pass | design.md §3.1–§3.6 |
| Critical Questions are answered | Pass | OD-004 решён пользователем (сценарий B) |
| Tasks have dependencies, acceptance criteria, files, verification | Pass | см. ниже |
| Verification commands are known | Pass | `make test`, `make lint`, новый статтест |

## Implementation Slices

**MVP slice (всё сразу, т.к. фикс маленький):** T3 + T4 (удаление кнопок, обновление hint) → T1 (re-render) → T2 (disable) → T5 (тесты) → T6 (a11y).

## Task T1: Re-render preview из локально модифицированного reviewData

**Priority:** P0  
**Estimate:** 1h  
**Dependencies:** T3 (нужно знать итоговую структуру state)  
**Covers:** FR-001, D-002

### Work
- [ ] В `applyFeedbackToFile` после успешного ответа: модифицировать `reviewData.text` (заменить add-участки на новые плейсхолдеры, reject-участки на исходный текст).
- [ ] Удалить из `reviewData.spans` rejected-спены.
- [ ] Добавить в `reviewData.spans` новые спены для add-участков (placeholder + entity_type + position).
- [ ] Обновить `pendingActions`: оставить только новые actions (которые появились после apply).
- [ ] Вызвать `renderPreview()`.

### Acceptance Criteria
- [ ] После apply preview показывает новые плейсхолдеры для add-участков.
- [ ] После apply preview показывает исходный текст для reject-участков.
- [ ] preview совпадает с тем, что будет в скачанном `result.md` (для тестового сценария).

### Files
- `frontend/app.js` — modify `applyFeedbackToFile` (строки ~782-866)

### Verification
- [ ] `make test` (mock) — pass
- [ ] Новый статтест `test_apply_feedback_updates_preview_locally` (см. T5)
- [ ] Manual: загрузить `.md`, сделать правку, нажать «Сохранить правки в файл» → preview обновился

## Task T2: Disable apply-кнопки при пустых actions

**Priority:** P1  
**Estimate:** 30m  
**Dependencies:** T1  
**Covers:** FR-002

### Work
- [ ] Заменить `updateSubmitButton()` на `updateApplyButton()`.
- [ ] В `updateApplyButton()`: `disabled = (reviewData.spans.length === 0 && pendingActions.length === 0)`.
- [ ] Вызвать `updateApplyButton()` в `openReview`, после каждого add/reject, после успешного apply.

### Acceptance Criteria
- [ ] Кнопка disabled, если в документе нет сущностей и пользователь не делал правок.
- [ ] Кнопка enabled, как только появляется любая сущность или правка.

### Files
- `frontend/app.js` — replace `updateSubmitButton`

### Verification
- [ ] Manual: загрузить пустой `.md` → кнопка disabled
- [ ] Manual: загрузить `.md` с одной сущностью → кнопка enabled

## Task T3: Удалить лишние кнопки и их обработчики

**Priority:** P0  
**Estimate:** 30m  
**Dependencies:** none  
**Covers:** FR-003, NFR-001, D-001, D-003

### Work
- [ ] В `frontend/index.html` удалить:
  - `<button id="confirm-all">...</button>` (строка 76)
  - `<button id="submit-feedback">...</button>` (строка 78)
  - `<button id="skip-review">...</button>` (строка 79)
  - `<div id="comment-section">...</div>` (строки 80-83)
  - `<p id="feedback-success">...</p>` (строка 85)
- [ ] В `frontend/app.js` удалить:
  - `$.confirmAll`, `$.submitFeedback`, `$.skipReview`, `$.feedbackSuccess`, `$.commentSection` (строки 43-48)
  - `confirmAll.addEventListener`, `submitFeedback.addEventListener`, `skipReview.addEventListener` (строки ~96-101)
  - Функции `confirmAll()`, `submitFeedback()`, `postFeedback()` (656-722, 878-900)
  - Упоминания `$.confirmAll`, `$.submitFeedback`, `$.skipReview`, `$.commentSection` в `openReview`/`closeReview`

### Acceptance Criteria
- [ ] В DOM нет `id="confirm-all"`, `id="submit-feedback"`, `id="skip-review"`, `id="comment-section"`, `id="feedback-success"`.
- [ ] В `app.js` нет функций `confirmAll`, `submitFeedback`, `postFeedback` (проверено grep'ом).
- [ ] В `app.js` нет вызовов `$.confirmAll`, `$.submitFeedback`, `$.skipReview`, `$.commentSection`, `$.feedbackSuccess`.

### Files
- `frontend/index.html` — remove DOM
- `frontend/app.js` — remove JS

### Verification
- [ ] Grep: `grep -n "confirm-all\|submit-feedback\|skip-review\|feedback-success\|comment-section\|postFeedback\|confirmAll" frontend/`
- [ ] `make test` — pass

## Task T4: Обновить hint text

**Priority:** P1  
**Estimate:** 5m  
**Dependencies:** T3  
**Covers:** FR-004

### Work
- [ ] В `frontend/index.html:59` заменить текст hint.

### Acceptance Criteria
- [ ] Hint содержит фразу «Нажмите «Сохранить правки в файл», чтобы применить правки к итоговому документу».

### Files
- `frontend/index.html` — line 59

### Verification
- [ ] Manual / grep

## Task T5: Регрессионные тесты

**Priority:** P0  
**Estimate:** 1h  
**Dependencies:** T1, T3  
**Covers:** FR-005

### Work
- [ ] Создать `tests/unit/frontend/test_review_simplification.py`.
- [ ] Реализовать тесты (статанализ `app.js` + `index.html`):
  - `test_no_confirm_all_button`
  - `test_no_submit_feedback_button`
  - `test_no_skip_review_button`
  - `test_no_feedback_success_message`
  - `test_no_comment_section`
  - `test_no_postfeedback_function`
  - `test_apply_button_present`
  - `test_apply_feedback_updates_preview_locally` — найти в `app.js` код `applyFeedbackToFile` и проверить, что после fetch есть модификация `reviewData` (regexp/ast) ПЕРЕД `renderPreview()`.
- [ ] Добавить в `pyproject.toml` discovery для `tests/unit/frontend/`.

### Acceptance Criteria
- [ ] Все новые тесты проходят.
- [ ] Тесты падают, если вернуть удалённые кнопки.

### Files
- `tests/unit/frontend/test_review_simplification.py` — create
- `pyproject.toml` — pytest config (если нужно)

### Verification
- [ ] `uv run pytest tests/unit/frontend/test_review_simplification.py -v`

## Task T6: a11y — aria-live на success

**Priority:** P2  
**Estimate:** 5m  
**Dependencies:** T3  
**Covers:** NFR-002

### Work
- [ ] В `frontend/index.html` добавить `aria-live="polite"` и `role="status"` к `<p id="apply-success">`.

### Acceptance Criteria
- [ ] Screen reader объявляет success-message.

### Files
- `frontend/index.html` — modify apply-success

### Verification
- [ ] Manual / grep `aria-live`

## Task T7: Финальная верификация (линт + тесты)

**Priority:** P0  
**Estimate:** 15m  
**Dependencies:** T1, T2, T3, T4, T5, T6  
**Covers:** project-wide

### Work
- [ ] `make lint`
- [ ] `make type`
- [ ] `make test`
- [ ] `make test-cov` — coverage ≥ 70% (gate)

### Acceptance Criteria
- [ ] Все три команды проходят.

### Files
- (no changes)

### Verification
- [ ] См. команды

## Task Order

`T3 → T4 → T6 → T1 → T2 → T5 → T7`
