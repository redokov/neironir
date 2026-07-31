# Tasks: Admin UI (F11)

> Requirements: @requirements.md
> Design: @design.md
> Status: Approved (adopt; реализация существует — задачи реверс-проверка)

## Requirement Coverage

| Requirement | Tasks | Notes |
|-------------|-------|-------|
| FR-001 | T1 | stats.py + GET /stats |
| FR-002 | T2 | list/detail documents |
| FR-003 | T3 | training start/status/stop |
| FR-004 | T4 | rules.py |
| FR-005 | T5 | settings GET/PUT |
| FR-006 | T6 | auth stack |
| FR-007 | T7 | admin.html/admin.js |
| FR-008 | T8 | тесты |
| NFR-001 | T6 | cookies/CSRF |
| NFR-002 | T3 | 409/stop-idle |

## Implementation Readiness Check

| Check | Status |
|-------|--------|
| Must Have → tasks | Pass |
| Covered by design | Pass |
| Questions answered | Pass (D-001…D-004) |
| AC/files/verification | Pass |

## Task T1: Статистика (существует)

**P0 · Covers:** FR-001
- [x] `admin/stats.py`: compute_documents_stats (period/buckets), compute_jobs_with_feedback
- [x] `GET /api/v1/admin/stats?period=&days=`

**Files:** `backend/neironir/admin/stats.py`, `admin/router.py`
**Verification:** `pytest tests/unit/admin/test_stats.py tests/integration/test_admin_api.py -q -k "Stats"`

## Task T2: Документы (существует)

**P0 · Covers:** FR-002
- [x] `GET /admin/documents?limit=` (сводки JobFeedbackSummary)
- [x] `GET /admin/documents/{job_id}` (drill-down; 404)

**Verification:** `pytest tests/integration/test_admin_api.py -q -k "Documents"`

## Task T3: Дообучение (существует)

**P0 · Covers:** FR-003, NFR-002
- [x] `admin/training.py`: TrainingState, start (dataset build + `opf train` subprocess), stop
- [x] Endpoints start/status/stop; 409 при running; stop при idle OK

**Verification:** `pytest tests/unit/admin/test_training.py tests/integration/test_admin_api.py -q -k "Training"`

## Task T4: Правила (существует)

**P0 · Covers:** FR-004
- [x] `api/rules.py`: list/stats/proposals/approve/reject/add-manual

**Verification:** `pytest tests/unit/... -k rules` (см. интеграционные контракты)

## Task T5: Runtime-настройки (существует)

**P1 · Covers:** FR-005
- [x] `GET/PUT /api/v1/admin/settings` + `_load/_save_runtime_timeout`

**Verification:** `pytest tests/integration/test_subprocess_fallback.py -q -k "Settings"`

## Task T6: Auth (существует)

**P0 · Covers:** FR-006, NFR-001
- [x] session.py (itsdangerous cookies), csrf.py, middleware, max_body_size
- [x] login/logout/whoami; require_admin_auth + verify_csrf на роутерах

**Verification:** `pytest tests/unit/auth/ tests/integration/test_auth_api.py -q`

## Task T7: Frontend (существует)

**P0 · Covers:** FR-007
- [x] `ui.py` GET /admin; admin.html/admin.js (stats, documents, training poll, rules, settings, logout)

**Verification:** `pytest tests/integration/test_admin_api.py -q -k "AdminUI"`

## Task T8: Тесты (существуют)

**P0 · Covers:** FR-008
- [x] unit/admin (stats, training, append), unit/auth (csrf, session), integration admin_api + auth_api

**Verification:** команды выше.

## Task T9: Реверс-проверка (актуальный шаг)

**P0 · 30m · Covers:** review
- [ ] Прогнать unit+integration тесты 003
- [ ] review.md; `.status` → review:done
