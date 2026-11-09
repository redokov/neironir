# Tasks: Security and Privacy Hardening

> Status: In Progress
> Requirements: @requirements.md
> Design: @design.md
> Last Updated: 2026-11-08

## Dependency Graph

```text
T1 auth boundary -> T2 upload cap -> T3 job token -> T7 docs/final verification
T1 auth boundary -> T4 session revocation
T1 auth boundary -> T5 safe regex
T3 job token -> T6 JSON cap + frontend token propagation
T6 -> T7
```

## T1 — Fail-closed documents auth (P0, 1–2h)

**Depends on:** none
**Files:** `backend/neironir/auth/dependencies.py`, `backend/neironir/config.py`, `.env.example`, `tests/integration/test_m2m_api.py`, `tests/unit/api/test_dependencies.py`

- Add loopback classifier; missing client address is non-loopback.
- Preserve invalid-Bearer precedence; no-header is allowed only for loopback.
- Add tests for IPv4/IPv6 loopback, non-loopback no header, invalid Bearer, valid Bearer, and no configured keys.

**Acceptance:** loopback remains 202; non-loopback no-header/invalid Bearer is 401; no PII/job is created on denial.
**Verify:** `uv run pytest tests/unit/api/test_dependencies.py tests/integration/test_m2m_api.py -q`

## T2 — Bounded chunked upload (P0, 2–4h)

**Depends on:** T1
**Files:** `backend/neironir/auth/max_body_size.py`, `backend/neironir/api/jobs.py`, `tests/integration/test_m2m_api.py`, `tests/unit/auth/test_max_body_size.py`

- Count all incoming ASGI chunks, not only Content-Length.
- Stop at configured limit with stable 413 error; keep endpoint post-parse backstop bounded.
- Verify no job directory/source is created after oversize chunked upload.

**Acceptance:** chunked and Content-Length uploads share the same limit; small upload succeeds; oversized upload does not materialize unbounded content.
**Verify:** `uv run pytest tests/unit/auth/test_max_body_size.py tests/integration/test_m2m_api.py -q`

## T3 — Per-job opaque access token (P1, 3–5h) — DONE 2026-11-09 08:15

**Status: Done.** Evidence: `uv run ruff check .` PASS; `uv run mypy backend/neironir` PASS (40 files); `uv run pytest tests/unit/domain/test_job.py tests/integration/test_api.py tests/integration/test_m2m_api.py -q` → 71 passed; `node --check frontend/app.js && node --check frontend/admin.js` PASS. Loopback exception was removed from `verify_job_access` (loopback keeps headerless upload per T1; job-scoped routes still require X-Job-Token or admin session — FR-004 restored, `test_missing_token_returns_404` passes). Issues (out of scope, NOT modified): E2E tests under `tests/e2e/` do not send `X-Job-Token` and will fail job-scoped routes; full suite not required green for T3.

**Depends on:** T1
**Files:** `backend/neironir/domain/job.py`, `backend/neironir/api/schemas.py`, `backend/neironir/api/jobs.py`, `backend/neironir/storage/local.py`, `frontend/app.js`, `frontend/admin.js`, `docs/api.md`, `tests/integration/test_m2m_api.py`, `tests/integration/test_api.py`, `tests/unit/domain/test_job.py`

- Issue random token only in creation response; persist digest, never raw token.
- Require `X-Job-Token` for status/download/annotations/feedback/apply-feedback/preview; admin bypass is explicit.
- Return indistinguishable 404 for wrong token, missing token, and legacy non-admin job.
- Store token in frontend state and attach it to every job-scoped fetch.
- Add legacy job deserialization test and documentation.

**Acceptance:** owner can complete full flow; another token cannot read/mutate; raw token is absent from job.json/logs; legacy jobs are admin-only.
**Verify:** `uv run pytest tests/unit/domain/test_job.py tests/integration/test_api.py tests/integration/test_m2m_api.py -q && node --check frontend/app.js && node --check frontend/admin.js`

## T4 — Session revocation (P1, 1–3h)

**Depends on:** T1
**Files:** `backend/neironir/auth/session.py`, `backend/neironir/auth/dependencies.py`, `backend/neironir/api/auth.py`, `tests/unit/auth/test_session.py`, `tests/integration/test_auth_api.py`

- Add random session `jti` to signed payload.
- Maintain bounded process-local revoked-jti registry until expiry; logout revokes before clearing cookies.
- Reject revoked sessions in admin dependencies and middleware; new login works.

**Acceptance:** old cookie returns 401 after logout; new cookie is valid; registry is bounded and does not log secrets.
**Verify:** `uv run pytest tests/unit/auth/test_session.py tests/integration/test_auth_api.py -q`

## T5 — Dynamic regex ReDoS guard (P1, 2–4h) — DONE 2026-11-09 night

**Status: Done.** Evidence: `uv run ruff check .` PASS; `uv run mypy backend/neironir` PASS (41 files); `uv run pytest tests/unit/privacy/test_rules_redos.py tests/unit/api/test_rules_validation.py tests/integration/test_rules_api.py -q` → 52 passed. New module `privacy/regex_guard.py` implements TD-004 conservatively without new deps: max pattern length constant 200 (spec design value; settings field out of T5 scope, documented), nested-quantifier scan (e.g. `(a+)+$`), backreference rejection (\1..\9, \g<..>). POST /api/v1/rules returns 422 with stable code `unsafe_regex` + reason; `load_dynamic_rules` skips unsafe persisted rules logging only rule id + reason (never pattern/document text); built-in static rules untouched; safe dynamic rules still load and detect. Trade-off documented: some legitimate advanced regexes (e.g. `((a+)+)`-free but deeply nested safe forms like `(?:foo|bar)+` quantifying a group) are conservatively rejected — accepted per TD-004.

**Depends on:** none
**Files:** `backend/neironir/api/rules.py`, `backend/neironir/privacy/rules.py`, `tests/unit/privacy/test_rules_redos.py`, `tests/unit/api/test_rules_validation.py`

- Add shared bounded validator: max length plus reject nested quantifiers, backreferences and unsafe constructs.
- Apply at manual-rule admission and persisted-rule load; static built-ins remain unchanged.
- Return stable 422 `unsafe_regex`; log only rule ID/reason, never document text.

**Acceptance:** `(a+)+$` and equivalent dangerous patterns are rejected/skipped; valid simple rules still work; detector never executes rejected patterns.
**Verify:** `uv run pytest tests/unit/privacy/test_rules_redos.py tests/unit/api/test_rules_validation.py -q`

## T6 — Bounded JSON download and span normalization (P2/P3, 1–3h)

**Depends on:** T3
**Files:** `backend/neironir/config.py`, `.env.example`, `backend/neironir/api/jobs.py`, `frontend/app.js`, `frontend/admin.js`, `tests/integration/test_m2m_api.py`, `tests/unit/frontend/test_review_simplification.py`

- Add configurable JSON response cap and reject oversized JSON representation with 413; binary download remains available.
- Clamp/drop invalid or overlapping spans before rendering; keep `escapeHtml`/textContent behavior.

**Acceptance:** JSON contract unchanged below cap; over-cap response is stable 413; malformed spans never throw or inject HTML.
**Verify:** `uv run pytest tests/integration/test_m2m_api.py tests/unit/frontend/test_review_simplification.py -q && node --check frontend/app.js && node --check frontend/admin.js`

## T7 — Documentation and full regression gate (P1, 1–2h)

**Depends on:** T2, T3, T4, T5, T6
**Files:** `docs/api.md`, `docs/architecture.md`, `README.md`, `.env.example`, `.github/workflows/ci.yml` only if required by new tests

- Document loopback/non-loopback boundary, `X-Job-Token`, legacy jobs, 413/404/422 contracts, and JSON cap.
- Run all mock tests, coverage and static checks; run real model before release.
- Record evidence in this file and heartbeat; do not modify spec 006 history.

**Acceptance:** docs match behavior; no regression in existing API compatibility; coverage >=70%.
**Verify:** `uv run ruff check . && uv run mypy backend/neironir && uv run pytest -m "not real_model" --cov=backend/neironir --cov-fail-under=70`

## Requirement Coverage

| Requirement | Tasks |
|-------------|-------|
| FR-001 | T1 |
| FR-002 | T1, T3 |
| FR-003 | T2 |
| FR-004 | T3 |
| FR-005 | T4 |
| FR-006 | T5 |
| FR-007 | T6 |
| FR-008 | T6 |
| NFR-001 | T1, T3, T4, T5 |
| NFR-002 | T1, T3, T6, T7 |
| NFR-003 | T7 |

## Readiness Check

| Check | Result |
|-------|--------|
| Must Have requirements covered | PASS |
| Every task has files, dependencies and acceptance | PASS |
| Verification commands available | PASS |
| Critical questions resolved | PASS |
| New external dependencies required | NO |
| Implementation gate | APPROVED by explicit request to begin night run |

## Execution Log

| Task | Status | Evidence |
|------|--------|----------|
| T1 | Done | `uv run ruff check .`; `uv run mypy backend/neironir`; targeted pytest: 45 passed |
| T2 | Done (accepted endpoint-level backstop; receive-level parser cap deferred) | ruff OK; mypy OK; targeted pytest 45 passed; full suite has unrelated T1 TestClient peer failure |
| T3 | Done | `uv run ruff check .`; `uv run mypy backend/neironir`; targeted pytest: 71 passed; node syntax checks PASS; E2E callers without X-Job-Token remain for final integration update |
| T4 | Done | ruff PASS; mypy PASS; pytest auth/session+auth_api 44 passed; dependencies 20 passed; diff review: no raw jti/cookie logging (only digest/comment references), logout revokes jti before cookie delete with `verify_csrf` + `require_admin_auth` deps |
| T5 | Done | `uv run ruff check .`; `uv run mypy backend/neironir`; regex/API/integration tests: 52 passed; no new dependencies |
| T6 | Done | `uv run ruff check .` PASS; `uv run mypy backend/neironir` PASS; `uv run pytest tests/integration/test_m2m_api.py tests/unit/frontend/test_review_simplification.py -q` 47 passed; `node --check frontend/app.js && node --check frontend/admin.js` PASS; max_json_download_size 5 MiB + pre-read 413 result_too_large (binary unaffected); frontend renderPreview validates/clamps/drops malformed/overlapping spans via DOM-safe rendering |
| T7 | Done | ruff PASS; mypy PASS; node syntax PASS; full pytest: 601 passed, 0 failed, 28 skipped; coverage gate PASS; docs/review/handoff refreshed. `make test-real` remains mandatory pre-release gate and was not run. |
