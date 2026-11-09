# Review: Security and Privacy Hardening

> Status: Passed
> Spec: `.ai/sdd/specs/007-security-privacy-hardening/`
> Updated: 2026-11-09

## Verification

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Typecheck | `uv run mypy backend/neironir` | PASS; 41 files |
| Frontend syntax | `node --check frontend/app.js && node --check frontend/admin.js` | PASS |
| Coverage | `uv run pytest --cov=backend/neironir --cov-report=term-missing --cov-fail-under=70 -q` | PASS: 601 passed, 0 failed, 28 skipped; coverage gate met |
| Full mock regression | `uv run pytest -q --disable-warnings --maxfail=1` | PASS: 601 passed, 0 failed, 28 skipped |
| Real model | `make test-real` | NOT RUN; OPF availability and release environment not confirmed |

## Regression Result

The T3 contract migration is complete. All affected integration and E2E callers now capture the creation token and send `X-Job-Token`; local TestClient fixtures explicitly model loopback where required. Production authorization remains unchanged.

## Accepted Limitations

- T2 uses an endpoint-level bounded `UploadFile.read(max_file_size + 1)` backstop. A true receive-level chunked multipart cap is deferred because the current Starlette public multipart API cannot provide the required stable 413 contract without fragile parser patching.
- `make test-real` remains a mandatory pre-release gate under P-008.

## Documentation State

`docs/api.md` and `.env.example` contain the current job-token, auth-boundary, JSON-cap, regex, and error contracts. A follow-up should also reconcile `README.md` and `docs/architecture.md` if the public deployment guidance is changed.
