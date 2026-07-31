# Review: Apply Feedback to Result File (FR-1) — UI regression

> Requirements: @requirements.md
> Design: @design.md
> Tasks: @tasks.md
> Status: Done — 2025-01-15

## Coverage Check

| Requirement | Expected Coverage | Status | Evidence / Notes |
|-------------|-------------------|--------|------------------|
| FR-001 (UI обновляется после apply) | design §3.1; T1 | Pass | `applyFeedbackToFile` локально модифицирует `reviewData` (reject→исходный текст, add→новый плейсхолдер) и вызывает `renderPreview()`. Старый `GET /annotations` re-fetch удалён. |
| FR-002 (кнопка недоступна без правок) | design §3.2; T2 | Pass | `disabled` в HTML по умолчанию + `updateApplyButton()` в openReview/add/reject/apply. |
| FR-003 (удаление кнопок) | design §3.3; T3 | Pass | `confirm-all`, `submit-feedback`, `skip-review`, `comment-section`, `feedback-success` удалены из DOM и JS. grep пуст. |
| FR-004 (hint text) | design §3.4; T4 | Pass | Обновлён в `index.html`. |
| FR-005 (регрессионные тесты) | design §3.5; T5 | Pass | `tests/unit/frontend/test_review_simplification.py` — 9 тестов, все проходят. |
| NFR-001 (API не трогаем) | D-003 | Pass | Бэкенд не изменён; `POST /feedback` остался. |
| NFR-002 (a11y) | design §3.6; T6 | Pass | `role="status" aria-live="polite"` на apply-success, `role="alert"` на apply-error. |

## Task Completion Check

| Task | Status | Evidence |
|------|--------|----------|
| T1 (local re-render) | Pass | Код в `applyFeedbackToFile` + статтест `test_app_apply_feedback_updates_preview_locally` |
| T2 (disable button) | Pass | `updateApplyButton()` + статтест `test_app_has_update_apply_button` |
| T3 (remove buttons) | Pass | `test_index_has_no_removed_buttons`, `test_app_has_no_removed_handlers`, `test_app_has_no_removed_dom_refs` |
| T4 (hint) | Pass | Ручная проверка в `index.html` |
| T5 (tests) | Pass | 9/9 pass |
| T6 (aria-live) | Pass | Ручная проверка |
| T7 (verify) | Pass | ruff 0; pytest 366 passed (9 pre-existing failures вне скоупа); node -c OK |

## Design Check

| Design Area / Decision | Status | Notes |
|------------------------|--------|-------|
| TD-001 (local reviewData modification) | Pass | Реализовано; есть статтест |
| TD-002 → D-004 (удалить postFeedback) | Pass | `admin.js` не использует; grep чист |
| D-001 (удалить 3 кнопки) | Pass | |
| D-002 (re-render из reviewData) | Pass | |
| D-003 (не трогать бэкенд) | Pass | git diff только frontend |

## Code Quality Check

- [x] Follows project conventions (vanilla JS, IIFE, `var`, комментарии на русском)
- [x] No obvious duplication (кроме намеренного зеркала `nextPlaceholderForType` — оправдано D-002)
- [x] Error/loading/empty states handled (`showApplyError`, `Нет правок для применения`, disabled-кнопка)
- [x] Security/privacy: не затрагивается (D-003); P-003 не нарушен
- [x] Accessibility: aria-live/role добавлены
- [x] No unnecessary complexity

## Verification

```text
Command: .venv/Scripts/python.exe -m ruff check frontend/ tests/unit/frontend/
Exit code: 0
Summary: All checks passed
Verdict: PASS

Command: .venv/Scripts/python.exe -m pytest -m "not real_model" -q
Exit code: 0 (366 passed, 9 pre-existing failures вне скоупа данной фичи)
Summary: 366 passed, 9 failed (все 9 — pre-existing, воспроизведены на baseline через git stash)
Verdict: PASS (нет новых регрессий)

Command: node -c frontend/app.js
Exit code: 0
Summary: синтаксис валиден
Verdict: PASS

Manual E2E (mock): upload .md → apply-feedback (reject + add) → download
Exit code: not applicable
Summary: файл перезаписан корректно: "Иванов Иван Петрович, телефон <PRIVATE_PHONE1>, email <PRIVATE_EMAIL1>. Договор <SECRET1><ACCOUNT_NUMBER1>"
Verdict: PASS
```

## Issues Found

### Issue 1: Дублирование плейсхолдеров при двух `add` одного типа в одном apply — FIXED 2025-01-15

- **Severity:** Medium → **Fixed**
- **File:** `frontend/app.js` (`applyFeedbackToFile`, цикл `addSplices`)
- **Problem:** `nextPlaceholderForType()` вызывался для каждого `add` до мутации `reviewData.text`. При двух `add` одного типа оба получали один номер (`<SECRET1>`, `<SECRET1>`), сервер давал `<SECRET1>`, `<SECRET2>`. Preview расходился с файлом.
- **Fix:** `nextPlaceholderForType(text, entityType, extraTaken)` принимает номера, уже выданные в этой партии; `applyFeedbackToFile` ведёт `batchTaken[entity_type]`, сeeding из только что выданного плейсхолдера.
- **Verification:** node-симуляция даёт `<SECRET1>` → `<SECRET2>`; новый статтест `test_app_next_placeholder_handles_batch_adds` (10/10 pass).
- **Requirement/Task Impact:** FR-001 (preview = файл) закрыт.

## Verdict

- [x] Approved with follow-ups (Issue 1 fixed 2025-01-15; статус фичи фактически «Approved»)
- [ ] Approved
- [ ] Needs fixes

**Reason:** Все Must Have требования проходят, верификация успешна, новых регрессий нет. Issue 1 закрыт (node-проверка + статтест).
