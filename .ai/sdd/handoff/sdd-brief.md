# SDD Handoff Brief: Programmatic (M2M) API

> Status: Done
> Readiness: Ready for release
> Updated: 2026-09-16

## Metadata

- **Spec ID:** `005-programmatic-api`
- **Spec Path:** `.ai/sdd/specs/005-programmatic-api/`
- **Current .status:** `review:done`
- **Source Inputs:**
  - `.ai/sdd/ideas/004-programmatic-api.md`
  - `.ai/steering/principles.md` (P-001/P-003/P-008/P-009/P-010/P-014)

## Product / Feature Summary

- **User / Audience:** внешние системы (скрипты, интеграции, CI), оператор сервиса
- **Problem:** пайплайн анонимизации был доступен только через браузерный UI (session/CSRF)
- **Outcome:** программный доступ: `Bearer <ключ>` → upload → poll → download (binary или JSON/base64)
- **Scope:** статические Bearer-ключи (`NEIRONIR_API_KEYS`), guard на `/api/v1/documents*`, контент-неготиация на `/download`, e2e на реальной модели
- **Out of Scope:** OAuth/ротация ключей, per-key права, rate limiting, M2M-доступ к admin/rules, новые форматы

## Requirements Summary

- **Key User Stories:** US-001 (полный M2M-флоу), US-002 (JSON-результат), US-003 (отказ 401), US-004 (неизменность UI)
- **Must Have Functional Requirements:** FR-001…FR-007 — все covered
- **Important NFRs:** NFR-001 (безопасность: ключи не логируются, compare_digest), NFR-002 (совместимость), NFR-003 (coverage ≥ 70%), NFR-004 (пустые ключи = M2M off) — все covered
- **Acceptance Notes:** Bearer-приоритет над cookie (TD-002); pass-through без Authorization-заголовка (TD-007, риск R-1 — задокументирован); feedback-эндпоинты вне M2M-контракта (TD-006)

## Design Summary

- **Approach:** одна FastAPI-dependency `require_documents_auth` на router `jobs.py`; без middleware
- **Components / Modules:** `auth/api_key.py` (parse/validate/get_bearer_token), `auth/dependencies.py`, `api/jobs.py` (`_wants_json`, `_content_disposition`), `api/schemas.py` (`DownloadResultResponse`), `config.py` (`api_keys`)
- **Data / State:** без изменений; ключи только в env, не персистятся
- **APIs / Integrations:** `POST /api/v1/documents/` → 202; `GET /{job_id}` → JobResponse; `GET /{job_id}/download` → binary | JSON по `Accept: application/json`
- **Technical Decisions:** TD-001 (единый 401), TD-002 (Bearer-приоритет), TD-003 (неготиация на download), TD-004 (без CSRF для Bearer), TD-005 (запятая-список), TD-006 (router-dependency), TD-007 (pass-through)
- **Risks / Constraints:** R-1 (эндпоинты открыты без заголовка — hardening как будущая фича), R-2 (base64 +33%), R-3 (утерянный ключ), R-4 (timing — compare_digest), R-5 (обратная совместимость download — закрыта регресс-тестами)

## Implementation Plan

- **Task Source:** `.ai/sdd/specs/005-programmatic-api/tasks.md` (T01–T13)
- **Recommended Order:** выполнено: T01–T03 (заглушки) → T04–T07 (тесты, red) → T08–T11 (реализация) → T13 (верификация) → T12 (докс)
- **Key Tasks:** все T01–T13 выполнены (см. review.md, Task Completion Check)
- **Likely Files / Areas:** перечислены в review.md, Review Scope

## Verification Plan

```text
Command: uv run pytest -m "not real_model" --cov=backend/neironir
Expected: 446 passed, 21 skipped, coverage ≥ 70% — PASS (TOTAL 87%)

Command: uv run pytest tests/integration/test_m2m_real_model.py -m real_model
  (env NEIRONIR_RUN_REAL_MODEL_TESTS=1, NEIRONIR_PRIVACY_FILTER_CMD=<абс. путь к opf.exe>)
Expected: PASS (факт: PASSED, 67 c, ф2.docx, PII-проверки)

Command: uv run ruff check . && uv run ruff format --check . && uv run mypy backend/neironir
Expected: 0 / 0 / 0 — PASS (факт)
```

- **Required Evidence:** все прогонные артефакты зафиксированы в `review.md` (Verification)
- **Acceptance Coverage:** FR-001…FR-007, NFR-001…004, US-001…004 — covered (см. review.md, Coverage Check)

## Review / Release Notes

- **Review Artifact:** `.ai/sdd/specs/005-programmatic-api/review.md`
- **Review Verdict:** Approved with follow-ups
- **Known Follow-ups:**
  - F-1 (Low): `any()`-early-exit в `is_valid_api_key` расходится с design §3.1/docstring — security-эффекта нет; выровнять при следующем касании
  - F-4 (Info): ручное построение Content-Disposition (`_content_disposition`, QA-фикс для кириллических имён) — занести в design при ревизии
  - F-3 (housekeeping): исполнителю заполнить чекбоксы/Execution Log в tasks.md
  - Вне спеки: hardening documents-эндпоинтов (R-1); окружение real-model (абсолютный путь к opf.exe)
  - Исправлено в ходе ревью: F-2 — docs/api.md дополнен описанием non-ASCII Content-Disposition fallback

## Handoff Readiness

- **Ready for Implementation:** yes (завершено)
- **Ready for QA:** yes (real-model e2e пройден)
- **Ready for Release:** yes (CHANGELOG 0.2.0 готов; блокеров нет)
- **Blockers:** N/A
- **Recommended Next Action:** merge/release 0.2.0; след. фича по PLAN.md
