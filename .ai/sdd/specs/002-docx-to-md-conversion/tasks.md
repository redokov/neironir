# Tasks: .docx → .md Conversion (F10)

> Requirements: @requirements.md
> Design: @design.md
> Status: Approved (adopt; реализация уже существует — таски реверс-проверка)

## Requirement Coverage

| Requirement | Tasks | Notes |
|-------------|-------|-------|
| FR-001 | T1 | Конвертер docx_to_md.py |
| FR-002 | T2 | _validate_output_format |
| FR-003 | T2 | default = source ext |
| FR-004 | T3 | UI checkbox |
| FR-005 | T4 | pipeline extracted.md |
| FR-006 | T5 | тесты unit/integration/e2e |
| NFR-001 | T4 | offset-модель |
| NFR-003 | T4 | privacy |

## Implementation Readiness Check

| Check | Status |
|-------|--------|
| Must Have requirements have tasks | Pass |
| Requirements covered by design | Pass |
| Critical questions answered | Pass (D-001…D-003) |
| Tasks have deps/AC/files/verification | Pass |
| Verification commands known | Pass |

## Implementation Slices

Adopt-слайс: **верификация существующей реализации** по 5 задачам + review.

## Task T1: Конвертер `docx_to_md.py` (существует)

**Priority:** P0 · **Estimate:** done · **Covers:** FR-001, D-001, D-002

- [x] `convert_to_markdown()` — заголовки, bold/italic, pipe-tables, hyperlink-collapse
- [x] `extract_markdown_runs()` — публичный итератор MarkdownElement
- [x] Проверено unit-тестами (24)

**Files:** `backend/neironir/converters/docx_to_md.py`
**Verification:** `pytest tests/unit/converters/test_docx_to_md.py -q`

## Task T2: Валидация форматов (существует)

**Priority:** P0 · **Estimate:** done · **Covers:** FR-002, FR-003

- [x] `output_format` Form-параметр
- [x] `_validate_output_format` — docx→md ок, md→docx 400, unknown 400
- [x] default (None) = source ext

**Files:** `backend/neironir/api/jobs.py`
**Verification:** `pytest tests/integration/test_output_format_and_apply_feedback.py -q -k "OutputFormatOnUpload"`

## Task T3: UI чекбокс (существует)

**Priority:** P1 · **Estimate:** done · **Covers:** FR-004

- [x] `#output-format-md` checked by default
- [x] `dataset.userSet` — выбор сохраняется между загрузками
- [x] `updateOutputFormatHint()`

**Files:** `frontend/index.html`, `frontend/app.js`
**Verification:** e2e `TestOutputFormatDefaultChecked`, `TestOutputFormatCheckboxSurvivesDrop`

## Task T4: Пайплайн docx→md (существует)

**Priority:** P0 · **Estimate:** done · **Covers:** FR-005, NFR-001, NFR-003

- [x] `_docx_to_markdown()` → `convert_to_markdown`
- [x] `extracted.md` промежуточный файл
- [x] `extracted_text.txt` + `annotations.json` по md-тексту

**Files:** `backend/neironir/workers/pipeline.py`
**Verification:** `pytest tests/integration/test_output_format_and_apply_feedback.py -q -k "DownloadRespectsOutputFormat or apply_feedback_on_docx"`

## Task T5: Тесты (существуют)

**Priority:** P0 · **Estimate:** done · **Covers:** FR-006

- [x] unit `test_docx_to_md.py` (24 теста)
- [x] integration `TestOutputFormatOnUpload` / `TestDownloadRespectsOutputFormat` / `TestApplyFeedbackEndpoint`
- [x] e2e `test_output_format_apply_feedback.py`

**Files:** `tests/unit/converters/`, `tests/integration/`, `tests/e2e/`
**Verification:** см. T1–T4

## Task T6: Реверс-проверка (актуальный шаг)

**Priority:** P0 · **Estimate:** 30m · **Covers:** review

- [ ] Прогнать unit/интеграционные тесты 002
- [ ] Зафиксировать результаты в review.md
- [ ] Выставить `.status` = review:done

**Verification:** команды ниже.
