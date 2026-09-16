# Review: Programmatic (M2M) API

> Requirements: @requirements.md
> Design: @design.md
> Tasks: @tasks.md
> Status: Done
> Reviewed: 2026-09-16

## Review Scope

- **Feature:** Programmatic (M2M) API for document anonymization (F15)
- **Implementation status:** implementation:done (фактически; `.status` до ревью отставал — `implementation:in-progress`, обновлён при этом ревью)
- **Files reviewed:**
  - `backend/neironir/auth/api_key.py` (новый)
  - `backend/neironir/auth/dependencies.py` (`require_documents_auth`)
  - `backend/neironir/api/jobs.py` (router dependency, download-неготиация, `_content_disposition`)
  - `backend/neironir/api/schemas.py` (`DownloadResultResponse`)
  - `backend/neironir/config.py` (`api_keys`, `api_key_set`)
  - `tests/unit/auth/test_api_key.py`, `tests/unit/api/test_schemas.py`
  - `tests/integration/test_m2m_api.py`, `tests/integration/test_m2m_real_model.py`, `tests/integration/test_api.py` (non-ASCII download)
  - `docs/api.md`, `.env.example`, `README.md`, `CHANGELOG.md`
- **Out of review scope:** UI-фронтенд, admin/rules/training-эндпоинты (NFR-002: не расширялись), `meta_router` (`/api/v1/mode` — вне префикса documents, не тронут).

## Coverage Check

| Requirement | Status | Evidence / Notes |
|-------------|--------|------------------|
| FR-001 (Bearer-auth) | **Covered** | `auth/api_key.py:parse_api_keys/is_valid_api_key`, `auth/dependencies.py:require_documents_auth`, `api/jobs.py` router `dependencies=[Depends(require_documents_auth)]`; unit: `test_api_key.py::TestIsValidApiKey/TestGetBearerToken`; integration: `test_m2m_api.py::TestM2MHappyPath`. Ключи не логируются — в `auth/` нет ни одного `logger`-вызова с токеном |
| FR-002 (отказ 401) | **Covered** | Единый `401 invalid_api_key` + `WWW-Authenticate: Bearer` (TD-001), `detail`-конверт `ErrorResponse`; тесты: wrong key / `Basic` / голый `Bearer` / ключи не настроены; `test_wrong_key_returns_401` проверяет `"wrong-key" not in r.text` (ключ не светится). Задокументировано: docs/api.md «Правила доступа» + таблица кодов |
| FR-003 (совместимость session/UI) | **Covered** | Pass-through без Authorization-заголовка; `TestM2MRegressionNoAuthorizationHeader` (3 теста); Bearer-приоритет над cookie: `TestM2MBearerPrecedence::test_invalid_bearer_with_valid_session_returns_401` (логин через `/login` → валидная cookie → невалидный Bearer → 401). Существующие session/CSRF-тесты (`test_auth_api.py` и др.) не правились и зелёные (446 passed) |
| FR-004 (асинхронный контракт) | **Covered** | Upload/get_job/download тела не менялись; 202/404/409/400/413 — `TestM2MErrorContract` (4 теста); `output_format` — без изменений (`_validate_output_format`) |
| FR-005 (два формата результата) | **Covered** | `_wants_json` точный match; `TestDownloadContentNegotiation` (5 тестов: без Accept / `*/*` / `jsonx` → бинарный; `;q=0.9` / `,*/*` → JSON); happy path сверяет `base64.b64decode(content_base64) == result_bytes`; 409 для не-completed (`test_download_pending_job_returns_409`) |
| FR-006 (документация) | **Covered** | docs/api.md — секция «Machine-to-machine API» (настройка, правила доступа, контракт-таблица, флоу с curl, оба формата, error-коды); `.env.example:NEIRONIR_API_KEYS` + комментарий; README.md «Machine-to-machine доступ»; CHANGELOG 0.2.0 |
| FR-007 (e2e real_model) | **Covered** | `test_m2m_real_model.py` (маркер `real_model`, 3 skip-гварда): полный флоу upload(Bearer)→poll→binary+JSON на `ф2.docx`, проверки `<PRIVATE_*`-плейсхолдеров и отсутствия исходных PII. Прогон: PASSED, 67 c |
| NFR-001 (безопасность) | **Covered** | `secrets.compare_digest` per key; сообщения 401 не содержат токен; `auth/`-модули не логируют ключи; тест-гарантия `"wrong-key" not in r.text`. См. Finding F-1 (early-exit в `any()`) — влияния на безопасность нет |
| NFR-002 (совместимость) | **Covered** | `require_documents_auth` только на `jobs.router`; `require_admin_auth`/`verify_csrf`/`AdminUIAuthMiddleware` не тронуты; ответы session-клиентов не изменились (регресс- suite зелёный); OpenAPI расширен (`responses` у download: два медиа-типа + `DownloadResultResponse`) |
| NFR-003 (тестируемость) | **Covered** | 446 passed / 21 skipped / 7 deselected; coverage TOTAL 87% ≥ 70%; новые модули: `auth/api_key.py` 100%, `require_documents_auth` покрыт полностью, download-неготиация покрыта |
| NFR-004 (пустые ключи = M2M off) | **Covered** | `parse_api_keys("") → frozenset()`; `test_unconfigured_keys_reject_any_bearer` + `test_requests_without_header_pass_when_keys_unconfigured`; сервис стартует без ошибок (TestClient в фикстурах) |

User stories: US-001 (happy path, mock + real), US-002 (JSON/base64), US-003 (401), US-004 (регресс без Authorization) — все **covered**.

## Task Completion Check

| Task | Status | Evidence / Notes |
|------|--------|------------------|
| T01–T03 (заглушки) | Pass | `config.py:api_keys/api_key_set`, `auth/api_key.py`, `schemas.py:DownloadResultResponse`, `dependencies.py:require_documents_auth` |
| T04–T05 (unit-тесты) | Pass | `tests/unit/auth/test_api_key.py` (24 теста: parse/validity/bearer/settings), `test_schemas.py::TestDownloadResultResponse` |
| T06 (integration) | Pass | `test_m2m_api.py` — 19 тестов, все сценарии design §10.2 |
| T07 (real-model e2e) | Pass | `test_m2m_real_model.py`, прогон PASSED |
| T08–T11 (реализация + wiring) | Pass | См. Coverage Check; router-level dependency (TD-006) |
| T12 (документация) | Pass | docs/api.md, .env.example, README.md, CHANGELOG.md 0.2.0 |
| T13 (финальная верификация) | Pass | См. Verification |

Примечание: чекбоксы в `tasks.md` и Execution Log не заполнены исполнителем (Finding F-3, housekeeping).

## Design Check

| Design Area / Decision | Status | Notes |
|------------------------|--------|-------|
| TD-001 (401 + WWW-Authenticate; 403 резерв) | Pass | `_api_key_unauthorized`, 403 не используется на documents |
| TD-002 (Bearer-приоритет без fallback) | Pass | Реализация + тест с реальным логином |
| TD-003 (неготиация на /download) | Pass | `_wants_json`, точный match, обратная совместимость |
| TD-004 (CSRF не для Bearer) | Pass | `verify_csrf` на documents-роутере не появился |
| TD-005 (одна env-переменная, запятая) | Pass | `NEIRONIR_API_KEYS` |
| TD-006 (dependency на router) | Pass | Весь `/api/v1/documents*` под guard'ом; feedback-эндпоинты вне M2M-контракта (задокументировано в docs/api.md) |
| TD-007 (pass-through без заголовка) | Pass | Задокументирован в docs/api.md («Правила доступа») — риск R-1 раскрыт |
| §9 Edge cases | Pass | `Basic xyz`/голый Bearer/пустой ключ/пустой список/`k1 , k2`/`jsonx` — покрыты тестами; битый UUID → 422 (стандарт FastAPI, в docs указано) |
| §7 Security | Pass | Ключи не в ответах/логах; timing-сравнение; см. F-1 |
| Design §3.5 `FileResponse(..., filename=...)` | Deviation (justified) | QA-фикс: Content-Disposition строится вручную через `_content_disposition()` (ASCII-fallback + RFC 5987 `filename*`) — см. Finding F-4. Плюс-правка к дизайну, не в нём описана |

## Code Quality Check

- [x] Follows project conventions (ruff 0, format 0, mypy 0)
- [x] No obvious duplication (`_http_error`/`_load_job_or_404` переиспользованы)
- [x] Error/loading/empty states handled (401/404/409/400/413/422)
- [x] Types are appropriate (`FileResponse | DownloadResultResponse`, mypy 0)
- [x] Security/privacy considerations are handled (см. Coverage NFR-001)
- [x] Accessibility — N/A (M2M, без UI)
- [x] No unnecessary complexity (хелперы чистые, без middleware)
- [x] Tests cover important behavior and edge cases (unit 24 + integration 19 + real e2e 1)

## Verification

```text
Command: uv run pytest -m "not real_model" --cov=backend/neironir
Exit code: 0
Summary: 446 passed, 21 skipped, 7 deselected; coverage TOTAL 87% (gate 70%) — PASS

Command: uv run pytest tests/integration/test_m2m_real_model.py -m real_model
  (env NEIRONIR_RUN_REAL_MODEL_TESTS=1, NEIRONIR_PRIVACY_FILTER_CMD=C:/MyProjects/neurodoc/.venv-opf/Scripts/opf.exe)
Exit code: 0
Summary: PASSED, 67 s — полный M2M-флоу на реальном документе ф2.docx с PII-проверкой — PASS

Command: uv run ruff check .
Exit code: 0 — 0 ошибок — PASS

Command: uv run ruff format --check .
Exit code: 0 — 132 files OK — PASS

Command: uv run mypy backend/neironir
Exit code: 0 — 0 issues — PASS
Verdict: PASS
```

Прогоны выполнены исполнителем до ревью; ревьюером выполнена выборочная дешёвая перепроверка по коду/тестам (соответствие реализаций тест-контрактам, отсутствие логирования ключей, wiring роутера).

Заметка окружения: real-model-прогон требует абсолютного пути к opf.exe в `NEIRONIR_PRIVACY_FILTER_CMD` (opf не на PATH; в `.env` путь относительный — из корня репо работает, из произвольного CWD — нет). Не блокер.

## Issues Found

### F-1: `any()` даёт early-exit на первом совпавшем ключе — расходится с формулировкой design §3.1 и docstring

- **Severity:** Low (не блокирует)
- **File:** `backend/neironir/auth/api_key.py:44` (`is_valid_api_key`)
- **Problem:** `any(secrets.compare_digest(token, key) for key in keys)` прекращает сравнение после первого match, тогда как design §3.1 и docstring функции декларируют «no early exit … on a match».
- **Requirement/Task Impact:** NFR-001 (частично), расхождение design ↔ код ↔ docstring.
- **Security-анализ:** практического влияния нет — «ключ валиден» и так публично видно по статус-коду (200 vs 401); compare_digest внутри каждой пары по-прежнему защищает от посимвольного timing-утечения; частичный-match (префикс) не даёт early-exit (все несовпавшие ключи сравниваются полностью). Число ключей мало (статический список), время сравнения строк пренебрежимо против сетевого джиттера.
- **Suggested Fix:** либо выровнять docstring/design с реализацией, либо заменить на `functools.reduce(lambda a, b: a or b, (compare_digest(token, key) for key in keys), False)`. Follow-up, не блокер.

### F-2: docs/api.md не описывал non-ASCII Content-Disposition fallback (QA-фикс не был задокументирован)

- **Severity:** Low (не блокирует)
- **File:** `docs/api.md` (секция «Выдача результата: два формата»)
- **Problem:** QA-фикс `_content_disposition()` (ASCII-fallback + RFC 5987 `filename*` для кириллических имён) реализован и протестирован (`test_download_non_ascii_filename_has_ascii_fallback`), но в документации упоминался только общий формат имени.
- **Requirement/Task Impact:** FR-006 (полнота документации контракта).
- **Fix:** **Fixed в ходе ревью** — в docs/api.md добавлено описание двух параметров Content-Disposition с примером для `ф2.docx`.

### F-3: tasks.md не заполнен исполнителем (чекбоксы `[ ]`, Execution Log пуст) и `.status` отставал

- **Severity:** Low (housekeeping)
- **File:** `.ai/sdd/specs/005-programmatic-api/tasks.md`, `.status`
- **Problem:** Все задачи фактически выполнены (доказательства — Verification выше), но артефакты трассировки не отражают этого. `.status` оставался `implementation:in-progress`.
- **Fix:** `.status` обновлён до `review:done` при этом ревью; tasks.md оставлен исполнителю (вне правок ревьюера по условиям задачи).

### F-4: Отступление от design §3.5 — Content-Disposition строится вручную, а не через `FileResponse(filename=...)`

- **Severity:** Info (positive deviation)
- **File:** `backend/neironir/api/jobs.py:download`, `_content_disposition`
- **Problem:** Дизайн фиксировал `return FileResponse(result_path, media_type=..., filename=...)`; реализация строит заголовок вручную ради RFC 6266 §4.3 ASCII-fallback для non-ASCII имён (QA-находка, воспроизводимая на кириллических файлах).
- **Requirement/Task Impact:** нет (расширение, не сужение контракта); бинарная ветка по-прежнему возвращает `FileResponse`.
- **Suggested Fix:** занести в design при следующей ревизии спеки (Follow-up).

## Positive Findings

- Точная и экономная архитектура: один dependency на роутере вместо middleware/per-endpoint (TD-006) — легко переопределяется в тестах, все `/documents*`-эндпоинты закрыты единообразно.
- Sentinel-семантика `get_bearer_token` (`None` vs `""`) чисто разделяет «нет заголовка» и «битая схема» — редкий пример аккуратного API-дизайна.
- Тесты кодируют контракт дословно: проверка отсутствия ключа в теле ошибки, реальный логин для precedence-теста, сравнение base64 с файлом на диске, PII-ассерты на реальной модели.
- Регресс-дисциплина: существующие session/e2e-тесты не правились ни на строку — FR-003/US-004 доказан самим suite'ем.
- QA-фикс non-ASCII имён сделан по стандартам (RFC 5987/6266) с юнит- и интеграционным покрытием.

## Follow-Ups

- F-1: выровнять docstring/design с `any()`-реализацией (или убрать early-exit) — при следующем касании `auth/api_key.py`.
- F-4: отразить `_content_disposition` в design.md при следующей ревизии спеки.
- F-3: исполнителю заполнить чекбоксы/Execution Log в tasks.md.
- (вне спеки, уже в design §13 R-1): hardening documents-эндпоинтов (закрыть pass-through для запросов без Authorization) — отдельная будущая фича.
- Окружение real-model: рассмотреть абсолютный путь к opf.exe в `.env` или документировать требование запуска из корня репо.

## Verdict

- [ ] Approved
- [x] Approved with follow-ups
- [ ] Needs fixes

**Reason:** Все 7 FR и 4 NFR covered с доказательствами (код + тесты + докс); все Must Have-требования проходят; верификация полностью зелёная (446 passed, coverage 87% > 70%, real-model e2e PASSED, ruff/mypy 0). Остались только Low/Info-находки: F-1 (early-exit, без security-эффекта), F-3 (housekeeping tasks.md), F-4 (info-отступление, зафиксировано). F-2 исправлен в ходе ревью. Готово к merge/release 0.2.0.
