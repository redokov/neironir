## Execution Log

| Task | Status | Evidence |
|------|--------|----------|
| T0 | Done | ruff OK; --spec 008 status prints 5 pending tasks; default (no flags) still spec 006 17/17 Done |
| T1 | Done | verify_job_access: header-only (query branch removed, docstring updated); test_download_rejects_access_token_query_param 404; pytest test_api.py 28 passed; ruff OK; mypy 41 files OK |
| T2 | Done | app.js: downloadResult() fetch+X-Job-Token+Blob/createObjectURL/revokeObjectURL, preventDefault, href token-free; node --check OK; tests/unit/frontend 24 passed (incl. 3 new static); ruff OK |
| T3 | Done | api.md: query-фолбэк заменён на header-only контракт + fetch-скачивание; CHANGELOG [Unreleased] ломающее; README/architecture не упоминали фолбэк; grep чист |
| T4 | Done | full mock suite 604 passed / 0 failed / 21 skipped; ruff OK; mypy 41 files OK; node --check app.js+admin.js OK |

Формат Status: `Done` / `blocked (причина)`. Evidence — вывод финальной
верификации задачи (ruff/mypy/pytest).

