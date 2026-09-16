# Tasks: Programmatic (M2M) API (F15)

> Requirements: @requirements.md
> Design: @design.md
> Status: Draft

Методология пользователя: **тесты → код → прогон на реальных данных → документация**.
Задачи на тесты (T05–T07) идут ДО задач на реализацию (T08–T11) и сначала падают (red).
Заглушки (T01–T04) — минимальные, чтобы тесты импортировались и коллекционировались.

Все команды — из корня репо `c:/MyProjects/neurodoc`, через `uv run`.
Тестовые прогоны mock-режима: `uv run pytest -m "not real_model" -q`.
Coverage-gate: `uv run pytest -m "not real_model" --cov=backend/neironir --cov-report=term-missing` ≥ 70% (P-009).

---

## Группа (a): инфраструктура-заглушки (компилируемость тестов)

### T01 — config.py: поле `api_keys` — P0 · 15m
- [ ] В `backend/neironir/config.py` (класс `Settings`, pydantic-settings) добавить поле и property:
```python
# --- Machine-to-machine API keys (static, comma-separated) ---
api_keys: str = ""  # env: NEIRONIR_API_KEYS


@property
def api_key_set(self) -> frozenset[str]:
    """Parsed, stripped, non-empty keys from the comma-separated string."""
    from neironir.auth.api_key import parse_api_keys

    return parse_api_keys(self.api_keys)
```
- Files: `backend/neironir/config.py`
- Verify: `uv run pytest tests/unit/test_config.py -q` — зелёный (регресс), `uv run mypy backend/neironir/config.py` → 0
- Dependencies: —

### T02 — заглушка `auth/api_key.py` — P0 · 15m
- [ ] Создать `backend/neironir/auth/api_key.py` с сигнатурами из design §3.1 и телами-заглушками (`raise NotImplementedError`):
```python
def parse_api_keys(raw: str) -> frozenset[str]: ...
def is_valid_api_key(token: str, keys: frozenset[str]) -> bool: ...
def get_bearer_token(request: Request) -> str | None: ...
```
- Files: `backend/neironir/auth/api_key.py`
- Verify: `uv run python -c "import neironir.auth.api_key"` — OK; `uv run mypy backend/neironir/auth/api_key.py` → 0
- Dependencies: T01

### T03 — заглушка `DownloadResultResponse` + require-заглушки — P0 · 20m
- [ ] `backend/neironir/api/schemas.py` — добавить модель (поля из design §3.5, добавить в `__all__`):
```python
class DownloadResultResponse(BaseModel):
    """JSON representation of a cleaned file (GET /download with Accept: application/json)."""

    job_id: UUID
    filename: str
    ext: Literal["md", "docx"]
    media_type: str
    size: int
    content_base64: str
```
- [ ] `backend/neironir/auth/dependencies.py` — добавить заглушку рядом с `require_admin_auth` (pass-through, чтобы существующий suite оставался зелёным):
```python
def require_documents_auth(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """STUB: validates Bearer API key if Authorization header is present. Implemented in T09."""
    return None
```
(добавить в `__all__` файла)
- Files: `backend/neironir/api/schemas.py`, `backend/neironir/auth/dependencies.py`
- Verify: `uv run pytest -m "not real_model" -q` — без новых падений; `uv run mypy backend/neironir` → 0
- Dependencies: T01

---

## Группа (b): unit-тесты (red до T08/T10)

### T04 — tests/unit/auth/test_api_key.py — P0 · 1h
- [ ] Новый файл `tests/unit/auth/test_api_key.py` (рядом с `test_csrf.py`/`test_session.py`). Случаи из design §10.1:
  - `parse_api_keys`: `""` → пустое множество; `"k1"` → `{"k1"}`; `"k1, k2"` → strip; `"k1,, k2"` → пустые отброшены; `" , "` → пусто.
  - `is_valid_api_key`: точное совпадение; чужой ключ; пустой токен → False; пустое множество → False.
  - `get_bearer_token`: `Authorization: Bearer tok` → `"tok"`; `Basic xyz` → `""` (sentinel); голый `Bearer` → `""`; пустой токен → `""`; заголовок отсутствует → `None`. Для Request использовать `starlette.requests.Request` со scope (`headers=[(b"authorization", b"...")]`).
  - `Settings(api_keys="a,b").api_key_set == frozenset({"a","b"})`.
- Files: `tests/unit/auth/test_api_key.py`
- Verify: `uv run pytest tests/unit/auth/test_api_key.py -q` — коллекционируется, падает (NotImplementedError) до T08, зелёный после.
- Dependencies: T02, T03

### T05 — tests/unit/api/test_schemas.py: DownloadResultResponse — P0 · 30m
- [ ] Дополнить существующий `tests/unit/api/test_schemas.py`: сериализация `DownloadResultResponse` (все поля, `ext` Literal, `job_id` UUID, `content_base64` ascii-строка; `model_dump` round-trip).
- Files: `tests/unit/api/test_schemas.py`
- Verify: `uv run pytest tests/unit/api/test_schemas.py -q` — зелёный уже после T03 (модель — данные, не логика).
- Dependencies: T03

---

## Группа (c): integration-тесты (TestClient, mock-privacy)

### T06 — tests/integration/test_m2m_api.py — P0 · 3h
- [ ] Новый файл `tests/integration/test_m2m_api.py`. Фикстура по образцу `tests/integration/test_auth_api.py`: `Settings(api_keys="test-key-1,test-key-2")` + `app.dependency_overrides` для `get_settings/get_storage/get_privacy` (mock). Сценарии из design §10.2:
  - happy path: upload с ключом (`Authorization: Bearer test-key-1`, multipart `.md`) → 202 → poll до `completed` → download binary (Content-Disposition, MIME) → download JSON (`Accept: application/json`: `content_base64` == base64 result-файла, `filename/ext/media_type/size`);
  - 401: неверный ключ / схема не Bearer (`Basic xyz`) / ключи не настроены (`api_keys=""`); проверка `WWW-Authenticate: Bearer`, код `invalid_api_key`, отсутствие значения ключа в теле ошибки;
  - приоритет (Q2): невалидный Bearer при валидной session-cookie → 401;
  - контент-неготиация: без Accept и с `*/*` → бинарный; `application/jsonx` → бинарный; `application/json; q=0.9` → JSON;
  - 404 `job_not_found` / 409 `job_not_ready` для download; 400 `unsupported_format` / 413 `file_too_large` для upload;
  - регресс: запросы без Authorization проходят как раньше.
- Files: `tests/integration/test_m2m_api.py`
- Verify: `uv run pytest tests/integration/test_m2m_api.py -q` — падает (red) до T08–T11, зелёный после. Регресс: `uv run pytest tests/integration/test_api.py tests/integration/test_e2e_md.py tests/integration/test_e2e_docx.py -q` — без правок этих файлов, зелёные (FR-003/FR-004).
- Dependencies: T03, T04

---

## Группа (d): e2e real_model-тесты

### T07 — tests/integration/test_m2m_real_model.py — P1 · 2h
- [ ] Новый файл `tests/integration/test_m2m_real_model.py`, маркер `real_model`, по образцу `tests/integration/test_pipeline_real_model.py` (subprocess-режим OPF). Документы: `C:\MyProjects\MyTasks\summerizer\Договоры\ф2.docx` (валидный docx с PII — вход). **НЕ использовать `ф3.cleaned.md` как вход** — это очищенный эталон; использовать его как источник ожиданий (плейсхолдеры, отсутствие исходных PII). Сценарий: upload с ключом → poll до `completed` → бинарный download + JSON base64; оба результата содержат плейсхолдеры `<PRIVATE_PERSON1>`-стиля и не содержат PII из источника.
- Files: `tests/integration/test_m2m_real_model.py`
- Verify: без env — `uv run pytest -m real_model --collect-only -q` — тест коллекционируется/skip; с env — см. T13.
- Dependencies: T03, T04

---

## Группа (e): реализация auth/api_key.py

### T08 — parse/validate/get_bearer_token — P0 · 45m
- [ ] Реализовать в `backend/neironir/auth/api_key.py` по design §3.1: split по запятой, strip, drop empties (`parse_api_keys`); `is_valid_api_key` через `secrets.compare_digest` по каждому ключу; `get_bearer_token` — парсинг `Authorization: Bearer <token>`, sentinel `""` для malformed-схемы, `None` при отсутствии заголовка. Ключи не логируются.
- Files: `backend/neironir/auth/api_key.py`
- Verify: `uv run pytest tests/unit/auth/test_api_key.py -q` — зелёный; `uv run mypy backend/neironir/auth/api_key.py` → 0
- Dependencies: T04

---

## Группа (f): реализация схем и download-неготиации

### T09 — require_documents_auth — P0 · 45m
- [ ] Реализовать в `backend/neironir/auth/dependencies.py` вместо заглушки (design §3.2, стиль `_unauthorized`): нет заголовка → pass-through; схема не Bearer/пустой токен → 401 `detail={"code": "invalid_api_key", "message": ...}`, `headers={"WWW-Authenticate": "Bearer"}`; пустой `api_key_set` → 401 («API keys are not configured»); невалидный ключ → 401. Успех → `request.state.auth_via = "api_key"`. Значение ключа — нигде (сообщение/логи/ответ). 403 не используется (TD-001).
- Files: `backend/neironir/auth/dependencies.py`
- Verify: `uv run pytest tests/integration/test_m2m_api.py -q -k "401 or auth"` — часть тестов зелёная; `uv run mypy backend/neironir/auth/dependencies.py` → 0
- Dependencies: T06, T08

### T10 — download: контент-неготиация + _wants_json — P0 · 1.5h
- [ ] `backend/neironir/api/jobs.py`:
  - хелпер `_wants_json(accept: str) -> bool` — точный match `application/json` (design §3.5);
  - `download(...)`: добавить параметр `request: Request`, return-type `FileResponse | DownloadResultResponse`; `404/409` — как раньше; JSON-ветка: `result_path = storage.job_dir(job_id) / f"result.{job.effective_output_ext}"`, `content_base64 = base64.b64encode(result_path.read_bytes()).decode("ascii")`, вернуть `DownloadResultResponse`; бинарная ветка без изменений;
  - OpenAPI: дополнить `responses` у download (`200` — два медиа-типа, модель `DownloadResultResponse`).
- Files: `backend/neironir/api/jobs.py`
- Verify: `uv run pytest tests/integration/test_m2m_api.py -q -k "json or download or negoti"` — зелёные; `uv run mypy backend/neironir/api/jobs.py` → 0
- Dependencies: T05, T06

---

## Группа (g): wiring

### T11 — router dependency — P0 · 30m
- [ ] `backend/neironir/api/jobs.py`: конструктор роутера →
```python
router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    dependencies=[Depends(require_documents_auth)],
)
```
`meta_router` и тела эндпоинтов не трогать (TD-006).
- Files: `backend/neironir/api/jobs.py`
- Verify: `uv run pytest tests/integration/test_m2m_api.py -q` — весь файл зелёный; `uv run pytest -m "not real_model" -q` — существующий suite без падений и без правок тестов (FR-003/US-004)
- Dependencies: T09, T10

---

## Группа (h): документация

### T12 — .env.example, docs/api.md, README, CHANGELOG — P1 · 1.5h
- [ ] `.env.example`: `NEIRONIR_API_KEYS=` + комментарий (ключи через запятую; пусто = M2M отключён; не логируются).
- [ ] `docs/api.md`: секция «Machine-to-machine API» — Bearer-auth, `NEIRONIR_API_KEYS`, flow upload→poll→download (интервал ≥ 2 c), оба формата выдачи, таблица 401/404/409 + 400/413, пример `curl`, приоритет Bearer над cookie (TD-002), пасс-тру без заголовка (TD-007, R-1), feedback-эндпоинты вне M2M-контракта (TD-006).
- [ ] `README.md`: краткое упоминание M2M-доступа и ссылки на docs/api.md.
- [ ] `CHANGELOG.md`: запись о фиче.
- Files: `.env.example`, `docs/api.md`, `README.md`, `CHANGELOG.md`
- Verify: ручная проверка секции против design §6/§8; `grep -n "NEIRONIR_API_KEYS" .env.example docs/api.md`
- Dependencies: T11

---

## Группа (i): финальная верификация

### T13 — Полная верификация (mock + real + coverage) — P0 · 1h
- [ ] `uv run ruff check .` → 0; `uv run ruff format --check .` → 0
- [ ] `uv run mypy backend/neironir` → 0
- [ ] `make check` (lint + type + test) → зелёный
- [ ] Coverage: `uv run pytest -m "not real_model" --cov=backend/neironir --cov-report=term-missing` ≥ 70%
- [ ] Real-model: `make test-real` (opf на PATH; env `NEIRONIR_RUN_REAL_MODEL_TESTS=1`) — `test_m2m_real_model.py` зелёный
- Files: отчёт в Execution Log ниже
- Dependencies: T12

---

## Requirement Coverage

| Requirement | Task IDs |
|---|---|
| FR-001 (Bearer-auth) | T02, T04, T08, T09, T11 |
| FR-002 (отказ 401) | T04, T06, T08, T09, T12 |
| FR-003 (совместимость session/UI) | T06 (регресс), T09, T11 |
| FR-004 (асинхронный контракт) | T06, T11 |
| FR-005 (два формата результата) | T03, T05, T06, T10 |
| FR-006 (документация) | T12 |
| FR-007 (e2e real_model) | T07, T13 |
| NFR-001 (безопасность) | T04, T08, T09 |
| NFR-002 (совместимость кодов/OpenAPI) | T06, T10, T11 |
| NFR-003 (тестируемость, coverage) | T04–T07, T13 |
| NFR-004 (пустые ключи = M2M off) | T04, T06, T09 |

## Readiness Check

| Check | Result |
|---|---|
| Все Must Have FR покрыты задачами | Pass (таблица выше) |
| Каждая задача: файлы, критерии, зависимости, verify-команда | Pass |
| Verify-команды известны (Makefile, pyproject) | Pass (`make check/test-real`, `uv run pytest ... --cov`) |
| Open-вопросы requirements закрыты дизайном | Pass (Q1–Q5 → TD-001…TD-005) |
| Порядок «тесты → код → реальные данные → докс» | Pass (T04–T07 → T08–T11 → T13 → T12; T12 после T11, реал-прогон в T13) |
| Блокирующие провалы | нет |

## Execution Log

| Task | Status | Evidence |
|------|--------|----------|
| — | — | — |
