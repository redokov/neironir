# SDD Handoff Brief: neironir — adopt-фаза Phase 2 (F09–F11)

> Status: Reviewed
> Readiness: F09–F11 реверс-задокументированы и проверены; релиз — после стабилизации pre-existing failures
> Updated: 2025-01-15

## Metadata

- **Specs:**
  - `001-apply-feedback-to-result` — `.ai/sdd/specs/001-apply-feedback-to-result/` (review:done)
  - `002-docx-to-md-conversion` — `.ai/sdd/specs/002-docx-to-md-conversion/` (review:done)
  - `003-admin-ui` — `.ai/sdd/specs/003-admin-ui/` (review:done)
  - `004-stabilize-phase2` — `.ai/sdd/specs/004-stabilize-phase2/` (review:done)
- **Current .status:** review:done (все три)
- **Source Inputs:** `.ai/sdd/ideas/001..003`; `.ai/sdd/PLAN.md` (OD-001…OD-004 приняты)

## Product / Feature Summary

- **User / Audience:** автор документа (F09–F10), администратор системы (F11).
- **Problem:** preview сбрасывался после apply (F09); docx-структура терялась (F10); отсутствовал UI для статистики/дообучения/правил (F11).
- **Outcome:** одна кнопка apply с локальным re-render preview; собственный docx→md конвертер (без pandoc); полный admin-UI с auth+CSRF.
- **Scope:** F09–F11 (post-MVP), adopt-реверс + 1 багфикс (F09).
- **Out of Scope:** новые фичи (Phase 3 отменена OD-003).

## Requirements Summary

- **F09 (001):** US-001/US-002; FR-001…FR-005; NFR-001/002.
- **F10 (002):** US-001/US-002; FR-001…FR-006; NFR-001…003.
- **F11 (003):** US-001…US-005; FR-001…FR-008; NFR-001…004.

## Design Summary

- **F09:** локальная модификация `reviewData` после apply (TD-001); удаление лишних кнопок; `nextPlaceholderForType` + batch-счётчик.
- **F10:** `converters/docx_to_md.py` (python-docx, headings/bold/italic/pipe-tables, hyperlink-collapse); `extracted.md` промежуточный.
- **F11:** `admin/router.py` + `stats.py` + `training.py`; `api/rules.py`; session auth (itsdangerous) + CSRF; vanilla JS SPA.
- **Technical Decisions:** D-001…D-004 в каждом спеке; `decisions.md` в 001.
- **Risks / Constraints:** pre-existing failures тестов (9) — вне скоупа фич; mypy pre-existing (19).

## Implementation Plan

- **F09:** реализован + багфикс Issue 1 (batch adds) — tasks T1–T7 done.
- **F10:** реализован — tasks T1–T6 (реверс-проверка) done.
- **F11:** реализован — tasks T1–T9 (реверс-проверка) done.

## Verification Plan

```text
F09: ruff frontend+tests (0); pytest tests/unit/frontend (10 passed); node -c OK; manual E2E mock — PASS
F10: pytest tests/unit/converters/test_docx_to_md.py + test_output_format_and_apply_feedback.py (44 passed, 2 skipped)
     pytest tests/e2e/test_output_format_apply_feedback.py (2 passed)
F11: pytest tests/unit/admin tests/unit/auth (58 passed); integration admin_api+auth_api (23 passed, 2 pre-existing fail)
```

## Review / Release Notes

- **Review Artifacts:** по одному `review.md` на фичу — все `Approved with follow-ups`.
- **Review Verdict:** Approved with follow-ups (все три).
- **Known Follow-ups:**
  1. ~~9 pre-existing failures тестов~~ — **Closed 2025-01-15** (004).
  2. ~~19 pre-existing mypy-ошибок~~ — **Closed 2025-01-15** (004).
  3. ~~`make test-real`~~ — **Passed 2025-01-15**: 6/6 real_model (OPF на машине).
  4. ~~19 playwright-e2e~~ — **Passed 2025-01-15**: playwright+chromium установлены, 19/19.
  5. 2 docx_to_md скипа — осознанные (зависят от русских Word-стилей в шаблоне).

## Handoff Readiness

- **Ready for Implementation:** yes (всё реализовано)
- **Ready for QA:** yes — pytest 376 passed / 0 failed, coverage 83%, e2e 19 passed
- **Ready for Release:** yes (mock+real) — real_model 6/6 passed, playwright e2e 19/19 passed, ruff/mypy/format 0
- **Blockers:** none.
- **Recommended Next Action:** зафиксировать pre-release чек в CI (или релизный тег). Phase 2 стабилизирована полностью.
