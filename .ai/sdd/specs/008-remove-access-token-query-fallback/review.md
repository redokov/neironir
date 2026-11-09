# Review: Remove `?access_token=` query fallback

> Status: Passed
> Spec: `.ai/sdd/specs/008-remove-access-token-query-fallback/`
> Updated: 2026-11-09

## Verification

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Typecheck | `uv run mypy backend/neironir` | PASS; 41 files |
| Frontend syntax | `node --check frontend/app.js && node --check frontend/admin.js` | PASS |
| Full mock regression | `uv run pytest -m "not real_model" -q` | PASS: 604 passed, 0 failed, 21 skipped |
| Real model | `make test-real` (opf из `.venv-opf/Scripts` в PATH) | PASS: 7 passed, 0 failed (625 deselected) |

## Regression Result

Токен задачи больше не покидает заголовки HTTP. Сервер принимает токен
только из `X-Job-Token` (ветка `?access_token=` в `verify_job_access`
удалена); скачивание из веб-интерфейса идёт через `fetch` с заголовком и
`Blob`/`URL.createObjectURL` с обязательным `revokeObjectURL`. Токен в
query-параметре — даже валидный — получает неразличимый `404 job_not_found`
(регресс-тест `test_download_rejects_access_token_query_param`). M2M-клиенты
и e2e-сьюты не затронуты (уже работали через заголовок). admin.js не менялся:
admin-интерфейс не скачивает job-файлы по job-токену.

## Coverage

| Requirement | Status | Evidence |
|---|---|---|
| FR-001 (query-токен → 404) | Done | T1: `verify_job_access` header-only; негативный integration-тест |
| FR-002 (fetch + Blob скачивание) | Done | T2: `downloadResult()` + 3 статических regression-теста |
| FR-003 (токен из состояния фронтенда) | Done | T2: переиспользование `jobTokenHeaders()`/`currentJobToken` |
| NFR-001 (неразличимый 404) | Done | T1: все пути отказа дают один `404 job_not_found` |
| NFR-002 (vanilla JS) | Done | T2: без новых зависимостей |
| NFR-003 (гейты зелёные) | Done | T4: 604/0 mock, 7/0 real, ruff/mypy/node PASS |

## Accepted Limitations

- **Ломающее изменение:** старые открытые ссылки
  `/download?access_token=...` перестают работать (404). Приемлемо pre-1.0,
  зафиксировано в CHANGELOG `[Unreleased]`.
- «Открыть в новой вкладке» / копирование адреса ссылки ведёт на
  `/download` без токена → 404; безопасно, токен в href не попадает.
- Опция короткоживущего download-ticket (идея 005) отклонена — fetch-схема
  закрывает потребность.

## Documentation State

`docs/api.md` (раздел «Job access token») описывает header-only контракт и
fetch-скачивание; `CHANGELOG.md` содержит `[Unreleased]` с пометкой ломающего
изменения. README/architecture не упоминали query-фолбэк и не менялись.
