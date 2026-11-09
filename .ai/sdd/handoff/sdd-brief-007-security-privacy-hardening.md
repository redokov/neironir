# SDD Handoff Brief: Security and Privacy Hardening

> Status: implementation:done
> Readiness: Implementation complete; release gate pending `make test-real`
> Updated: 2026-11-09

## Sources

- Requirements: `.ai/sdd/specs/007-security-privacy-hardening/requirements.md`
- Design: `.ai/sdd/specs/007-security-privacy-hardening/design.md`
- Tasks: `.ai/sdd/specs/007-security-privacy-hardening/tasks.md`
- Review: `.ai/sdd/specs/007-security-privacy-hardening/review.md`
- Status: `.ai/sdd/specs/007-security-privacy-hardening/.status`
- Review inputs: `code-review-report.md`, `code-review-agent-report.md`

## Completed Tasks

- **T1:** fail-closed documents boundary: loopback anonymous upload flow, non-loopback Bearer requirement.
- **T2:** endpoint-level bounded upload read accepted; receive-level multipart cap deferred.
- **T3:** opaque per-job token, digest-only persistence, token required on job-scoped routes.
- **T4:** session jti and logout revocation.
- **T5:** dynamic regex safety guard with `unsafe_regex`.
- **T6:** JSON download size cap and malformed-span normalization.

## Final Task

- **T7:** complete. All remaining E2E/integration/unit callers were migrated to the T3 `X-Job-Token` contract. Full pytest is green: 601 passed, 0 failed, 28 skipped.

## Verification

- `uv run ruff check .` — PASS
- `uv run mypy backend/neironir` — PASS (41 files)
- `node --check frontend/app.js && node --check frontend/admin.js` — PASS
- `uv run pytest --cov=backend/neironir --cov-fail-under=70 -q` — PASS; 601 passed, 0 failed, 28 skipped; coverage threshold met
- `make test-real` — not run; mandatory before release under P-008

## Release Follow-up

Run `make test-real` with OPF before the release tag. Do not weaken production authorization or reintroduce loopback bypass for job-scoped routes.
