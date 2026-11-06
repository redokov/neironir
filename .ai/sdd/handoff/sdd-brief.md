# SDD Handoff Brief: Code Review Fixes

> Status: tasks:approved (binding)
> Readiness: Ready for implementation (ночной прогон)
> Updated: 2026-10-28

## Metadata

- **Spec ID:** `006-code-review-fixes`
- **Spec Path:** `.ai/sdd/specs/006-code-review-fixes/`
- **Current .status:** `tasks:approved`
- **Source Inputs:**
  - `code-review-agent-report.md` — 31 находка (opus-5.5 ревью `wf_20b0018e3454` + кросс-ревью deepseek `wf_abc5f66aeeee`)
  - Предыдущий спек 005 (Programmatic API) — review:done, готов к release 0.2.0 (его brief архивирован в `specs/005-programmatic-api/review.md`)

## Summary

- **Problem:** агент-ревью подтвердило 29 дефектов (9 high — консенсус двух моделей), включая 3 утечки PII в DOCX-санитизации.
- **Outcome:** 17 задач T1–T17 (P0→P3) + 3 deferred (ML, вне этого прогона). 2 находки опровергнуты кросс-ревью — исключены.
- **Scope:** `backend/neironir/converters/docx.py`, `workers/feedback_applier.py`, `api/jobs.py`, `api/schemas.py`, `api/auth.py`, `storage/local.py`, `auth/api_key.py`, `admin/training.py`, `frontend/{app,admin,login}.js` + regress-тесты к каждой задаче.
- **Out of Scope:** D1–D3 (opf: окна, spans, dead code), опровергнутые #14/#15, рефакторинг за пределами скоупа задач.

## Task Order (см. tasks.md — источник истины)

T1→T2→T3 (P0, docx.py; T3 после T1 — общий element map) → T4–T8 (P1, feedback) → T9–T12 (P2, frontend) → T13–T17 (P3, hardening). Прогресс фиксируется в Execution Log (tasks.md) + heartbeat `docs/night-006-heartbeat.log`.

## Verification Plan

```text
После каждой задачи:
  uv run ruff check .          → 0
  uv run mypy backend/neironir → 0
  uv run pytest -m "not real_model" → all passed
Финал: полный make check-эквивалент + git status-дифф.
```

## Night Run

- Рецепт и промт: `.ai/sdd/specs/006-code-review-fixes/night-run.md`
- Взвод: `arm_cont_after_reset.py "$PI_SESSION_FILE" --repeat 8 --ttl 20`
- Правила: 2-попытки → blocked; «продолжи» = норма; завершение = итог + снятие флага.

## Handoff Readiness

- **Ready for Implementation:** yes
- **Blockers:** N/A
- **Recommended Next Action:** ночной прогон по night-run.md; утром — morning-check (arms.log + heartbeat + Execution Log)
