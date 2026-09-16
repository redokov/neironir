# Design: Programmatic (M2M) API (F15)

> Requirements: @requirements.md
> Status: Draft

## 1. Summary

Программный доступ к пайплайну анонимизации через статические Bearer-ключи на существующих эндпоинтах `/api/v1/documents`. Без нового префикса, без middleware: одна dependency `require_documents_auth` на router `jobs.py`, новое поле `Settings.api_keys`, контент-неготиация JSON/base64 на `GET /{job_id}/download`. UI-флоу не меняется ни на байт.

## 2. Requirements Mapping

| Requirement | Design Coverage |
|-------------|-----------------|
| FR-001 | §3.1 `auth/api_key.py`, §3.2 `require_documents_auth`, §7 таблица |
| FR-002 | §3.2 (401 + `WWW-Authenticate: Bearer`), §9 edge cases, §3.6 docs/api.md |
| FR-003 | §3.4 (порядок проверки, Q2), §8 (совместимость) |
| FR-004 | §3.3 (upload/get_job не меняются; таблица §7) |
| FR-005 | §3.5 `DownloadResultResponse` + контент-неготиация (Q3) |
| FR-006 | §3.6 docs/api.md + .env.example |
| FR-007 | §10.3 test_m2m_real_model.py (маркер `real_model`, документы из «Договоры») |
| NFR-001 | §3.1 (compare_digest), §7 (без логирования ключей) |
| NFR-002 | §8 — ответы/коды для session-клиентов не меняются; OpenAPI расширяется |
| NFR-003 | §10 тестовая стратегия |
| NFR-004 | §3.1 (пустая строка → пустое множество → Bearer-путь всегда 401; UI работает) |

## 3. Technical Approach

### 3.1 Конфигурация и парсинг ключей (`config.py`, новый `auth/api_key.py`)

`backend/neironir/config.py` — новое поле и property:

```python
# --- Machine-to-machine API keys (static, comma-separated) ---
api_keys: str = ""  # env: NEIRONIR_API_KEYS


@property
def api_key_set(self) -> frozenset[str]:
    """Parsed, stripped, non-empty keys from the comma-separated string."""
```

(или парсинг вынести в `auth/api_key.py: parse_api_keys(raw: str) -> frozenset[str]`, а property делегирует — предпочтительно, чтобы логика была unit-тестируемой без Settings).

Новый модуль `backend/neironir/auth/api_key.py`:

```python
def parse_api_keys(raw: str) -> frozenset[str]:
    """Split comma-separated, strip whitespace, drop empties."""


def is_valid_api_key(token: str, keys: frozenset[str]) -> bool:
    """Constant-time comparison (secrets.compare_digest per key)."""


def get_bearer_token(request: Request) -> str | None:
    """Return the token from 'Authorization: Bearer <token>' or None.
    Returns the sentinel '' (empty string) for a malformed scheme
    (e.g. 'Basic xyz', bare 'Bearer', empty token)."""
```

Пустая строка ключа (пробелы) в списке отбрасывается; `NEIRONIR_API_KEYS=""` → пустое множество → M2M-доступ отключён (NFR-004).

### 3.2 Dependency `require_documents_auth` (`auth/dependencies.py`)

Новый guard рядом с `require_admin_auth` (тот же файл, тот же стиль `_unauthorized`):

```python
def require_documents_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Validate the Bearer API key IF an Authorization header is present.

    Precedence (Q2): a present Authorization header ALWAYS takes the
    API-key path — the session cookie is never consulted as a fallback.
    No Authorization header → pass-through (existing UI/local behaviour).
    """
```

Логика:

1. `header = request.headers.get("authorization")`; если `None` → `return` (pass-through, как сейчас).
2. `token = get_bearer_token(request)`; если схема не `Bearer` или token пуст → **401**, `detail={"code": "invalid_api_key", "message": ...}`, `headers={"WWW-Authenticate": "Bearer"}` (Q1).
3. Если `settings.api_key_set` пуст → **401** `invalid_api_key` (M2M отключён; сообщение «API keys are not configured», без значения токена).
4. Если `not is_valid_api_key(token, settings.api_key_set)` → **401** `invalid_api_key` + `WWW-Authenticate: Bearer` (Q1).
5. Успех → `request.state.auth_via = "api_key"`; `return`.

**403 не используется** — зарезервирован под будущие per-key права (Q1). Тело ошибки — существующий конверт `ErrorResponse` (`detail={"code","message"}`), как в `_unauthorized` из `auth/dependencies.py`. Значение ключа **никогда** не попадает в сообщение, логи или ответ.

### 3.3 Применение к роутеру (`api/jobs.py`)

В `jobs.py` меняем конструктор роутера (уровень роутера, не per-endpoint — см. TD-006):

```python
router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    dependencies=[Depends(require_documents_auth)],
)
```

- Функционально эндпоинты `upload`, `get_job`, `download` не меняются: их тела, валидация (`_ALLOWED_EXTS`, `max_file_size`, `_validate_output_format`), `BackgroundTasks` + `run_job`, `LocalStorage` — как есть.
- `meta_router` (`GET /api/v1/mode`) **не трогаем** — он вне префикса `/documents`, нужен только UI-баннеру.
- **M2M-контракт** (документируется в docs/api.md, гарантии стабильности) — только `POST /`, `GET /{job_id}`, `GET /{job_id}/download`. `annotations`, `preview`, `feedback`, `apply-feedback` — человеческие review-эндпоинты: Bearer-заголовок на них валидируется тем же роутерным guard (иначе невалидный ключ проходил бы на часть `/documents*` — нарушение FR-002), но эти эндпоинты **исключены из M2M-контракта** (Out of Scope в requirements: «доступ M2M к feedback — только базовый пайплайн»), не документируются для машин и могут меняться без уведомления.

### 3.4 Порядок auth-проверок (Q2, Q4)

```text
request to /api/v1/documents*
  ├─ Authorization header present?
  │    ├─ да → API-key path ONLY: валиден? → 200/2xx; невалиден → 401
  │    │        (session cookie игнорируется, тихого fallback НЕТ — Q2)
  │    └─ нет → pass-through (существующее поведение; session не проверялся и раньше)
  └─ CSRF: verify_csrf на documents-роутере отсутствует и не добавляется (Q4);
           Bearer-запросы к unsafe-методам CSRF-free by design (auth не cookie-based).
           AdminUIAuthMiddleware / MaxBodySizeMiddleware — без изменений.
```

### 3.5 JSON-выдача результата (Q3): контент-неготиация на `download`

`backend/neironir/api/schemas.py` — новая модель:

```python
class DownloadResultResponse(BaseModel):
    """JSON representation of a cleaned file (GET /download with Accept: application/json)."""

    job_id: UUID
    filename: str  # тот же download-name, что в Content-Disposition ("<stem>.cleaned.<ext>")
    ext: Literal["md", "docx"]
    media_type: str  # из _media_type_for(output_ext)
    size: int  # размер result-файла в байтах
    content_base64: str  # base64.b64encode(result_bytes).decode("ascii")
```

(добавить в `__all__`).

`api/jobs.py::download` — расширение (сигнатура и поведение бинарной ветки не меняются):

```python
async def download(
    job_id: UUID,
    request: Request,  # новый параметр
    storage: LocalStorage = Depends(get_storage),
) -> FileResponse | DownloadResultResponse:
    job = _load_job_or_404(storage, job_id)  # 404 job_not_found — как раньше
    if job.status != JobStatus.COMPLETED:  # 409 job_not_ready — как раньше
        ...
    if _wants_json(request.headers.get("accept", "")):
        result_path = storage.job_dir(job_id) / f"result.{job.effective_output_ext}"
        content = result_path.read_bytes()
        return DownloadResultResponse(..., content_base64=base64.b64encode(content).decode("ascii"))
    return FileResponse(result_path, media_type=..., filename=...)  # как сейчас
```

Хелпер `_wants_json(accept: str) -> bool` в `jobs.py`:

```python
def _wants_json(accept: str) -> bool:
    """True iff any media-range in the Accept header is exactly 'application/json'."""
    return any(
        entry.split(";", 1)[0].strip().lower() == "application/json"
        for entry in accept.split(",")
        if entry.strip()
    )
```

Правила: `Accept: application/json` (в т.ч. `application/json; q=0.9`, `application/json, */*`) → JSON; **любой другой Accept** (включая `*/*`, отсутствие заголовка) → бинарный файл. Точный match вместо substring — чтобы `application/jsonx` не матчился. Старые клиенты без Accept → бинарный файл (обратная совместимость FR-005). OpenAPI: дополнить `responses` у download: `200` с описанием двух форм медиа-типов и моделью `DownloadResultResponse`.

### 3.6 Документация (FR-006)

- `docs/api.md` — новая секция «Machine-to-machine API»: Bearer-auth, конфиг `NEIRONIR_API_KEYS`, flow upload→poll→download, оба формата выдачи, таблица 401/403/404/409, пример `curl`.
- `.env.example` — `NEIRONIR_API_KEYS=` с комментарием (ключи через запятую; пусто = M2M отключён).

## 4. Component / Module Structure

```text
backend/neironir/
  config.py                    # + api_keys: str = "" (+ api_key_set / parse делегируется)
  auth/
    api_key.py                  # NEW: parse_api_keys, is_valid_api_key, get_bearer_token
    dependencies.py             # + require_documents_auth
  api/
    jobs.py                     # router dependency; download(): Request + _wants_json + JSON-ветка
    schemas.py                  # + DownloadResultResponse
docs/api.md                     # M2M-секция
.env.example                    # NEIRONIR_API_KEYS
tests/unit/auth/test_api_key.py             # NEW
tests/integration/test_m2m_api.py           # NEW
tests/integration/test_m2m_real_model.py   # NEW (маркер real_model)
```

Изменений в `main.py`, `workers/`, `storage/`, `privacy/`, frontend — **нет**.

## 5. Data Model / State

Без изменений: Job/LocalStorage как есть. Ключи — только в Settings (env), не персистятся, не логируются. Нового состояния нет.

## 6. API / Integration Contract

Таблица эндпоинт × auth × статус-коды (M2M-контракт выделен):

| Endpoint | Валидный Bearer | Bearer невалиден/пустые ключи | Без Authorization (UI/локально) | Коды успеха | Коды ошибок |
|---|---|---|---|---|---|
| `POST /api/v1/documents/` *(M2M)* | 202 JobResponse | 401 | 202 (как сейчас) | 202 | 401, 400 (`unsupported_format`, `unsupported_output_format`), 413 `file_too_large` |
| `GET /api/v1/documents/{job_id}` *(M2M)* | 200 JobResponse | 401 | 200 (как сейчас) | 200 | 401, 404 `job_not_found` |
| `GET /api/v1/documents/{job_id}/download` *(M2M)* | 200: бинарный или JSON(base64) по Accept | 401 | 200: бинарный (как сейчас) | 200 | 401, 404 `job_not_found`, 409 `job_not_ready` |
| `GET /{job_id}/annotations` *(UI)* | 200 (вне M2M-контракта) | 401 | 200 (как сейчас) | 200 | 401, 404 |
| `GET /{job_id}/preview` *(UI)* | 200 (вне M2M-контракта) | 401 | 200 (как сейчас) | 200 | 401, 404 |
| `POST /{job_id}/feedback`, `POST /{job_id}/apply-feedback` *(UI)* | 200 (вне M2M-контракта) | 401 | 200 (как сейчас) | 200 | 401, 404, 409/400 как сейчас |
| `GET /api/v1/mode` | — (без изменений, вне префикса documents) | — | 200 | 200 | — |

Все ошибки — конверт `ErrorResponse` в `detail`; все 401 Bearer-ошибки несут `WWW-Authenticate: Bearer`.

## 7. Security / Permissions / Privacy

- Ключи только в env/`.env` (`.gitignore`); не в коде, не в БД (её нет), не в ответах.
- Сравнение через `secrets.compare_digest` — без timing-leak.
- Логирование: только факт «API key rejected/accepted» + `job_id`; **значение ключа и заголовок Authorization не логируются нигде** (ни в neironir, ни в новых местах; uvicorn access-log заголовки не пишет).
- Сообщения 401 не содержат присланный токен.
- PII-поверхность не расширяется: JSON-выдача отдаёт тот же `result.{ext}`, карта замен по-прежнему не хранится (P-001/P-003).
- CSRF: не применяется к Bearer-запросам (Q4); session/CSRF-механизмы admin/rules не ослабляются.

## 8. User Flows

M2M-клиент (тексты запросов):

1. **Upload:** `POST /api/v1/documents/`, headers `Authorization: Bearer <key>`, body multipart `file` (+ опц. `output_format`) → `202` `JobResponse` (`id`, `status=pending`).
2. **Poll:** `GET /api/v1/documents/{job_id}` с тем же ключом → `JobResponse.status`: `pending → processing → completed | failed`; рекомендуемый интервал ≥ 2 c (таймаут OPF до 600 с). `failed` — терминальный, смотреть `error`.
3a. **Download binary:** `GET /api/v1/documents/{job_id}/download` (без Accept / `Accept: */*`) → `200` файл (`Content-Disposition`, MIME по `_media_type_for`).
3b. **Download JSON:** тот же URL с `Accept: application/json` → `200` `DownloadResultResponse` (`content_base64` + filename/ext/media_type/size).

Любой шаг: невалидный ключ → `401` (+ повтор не поможет, ключи статические); job не найден → `404`; job не `completed` → `409`.

UI-клиент: полностью без изменений — те же URL, никаких новых заголовков, бинарный download по умолчанию.

## 9. Edge Cases

| Case | Expected |
|------|----------|
| Заголовок отсутствует | Pass-through — как сегодня (UI/local); см. §11 «Противоречия», риск R-1 |
| `Authorization: Basic xyz` / голый `Bearer` / пустой токен | 401 `invalid_api_key` + `WWW-Authenticate: Bearer` |
| Ключ не из списка | 401 `invalid_api_key` (без значения ключа в сообщении/логах) |
| `NEIRONIR_API_KEYS` пуст + Bearer присутствует | 401 (M2M отключён, NFR-004); UI продолжает работать |
| Bearer + валидная session cookie, Bearer невалиден | 401 — приоритет Bearer, без fallback (Q2) |
| Bearer валиден + session cookie | API-key путь; session игнорируется |
| job_id не существует / битый UUID | 404 `job_not_found` (несуществующий) / 422 (невалидный UUID — стандарт FastAPI) |
| download до завершения (`pending`/`processing`) | 409 `job_not_ready` — и для бинарной, и для JSON-ветки |
| download для `failed` | 409 `job_not_ready` (существующий контракт) |
| Файл > `MAX_FILE_SIZE` / не `.md`/`.docx` | 413 `file_too_large` / 400 `unsupported_format` — без послаблений для M2M |
| `Accept: application/jsonx` или `application/xml` | Бинарный файл (точный match на `application/json`) |
| `output_format` несовместим | 400 `unsupported_output_format` — как сейчас |
| Ключ с пробелами в списке (`k1 , k2`) | Нормализуется `parse_api_keys` (strip) |

## 10. Verification Strategy

### 10.1 Unit (`make test`, mock)

- `tests/unit/auth/test_api_key.py`: `parse_api_keys` (пусто/один/несколько/пробелы/пустые элементы), `is_valid_api_key` (точное совпадение, чужой ключ, пустой токен, пустое множество), `get_bearer_token` (валидный, `Basic`, голый `Bearer`, отсутствующий заголовок).
- `tests/unit/api/test_schemas.py`: сериализация `DownloadResultResponse`.

### 10.2 Integration (TestClient, mock-privacy; `make check`)

`tests/integration/test_m2m_api.py` (fixture по образцу `test_auth_api.py`: `Settings(api_keys="test-key-1,test-key-2")`, overrides `get_settings/get_storage/get_privacy`):

- happy path: upload с ключом → 202 → poll до `completed` → download binary → download JSON (`Accept: application/json`, проверка base64 == содержимому result-файла, filename/media_type/size);
- 401: ключ неверный / схема не Bearer / ключи не настроены; наличие `WWW-Authenticate: Bearer`; ключ не светится в теле ошибки;
- приоритет: невалидный Bearer при валидной session cookie → 401;
- контент-неготиация: без Accept и с `*/*` → бинарный; `application/jsonx` → бинарный;
- 404/409 для download; 400/413 для upload;
- регресс совместимости: запросы без Authorization проходят (существующие `test_api.py` / `test_e2e_md.py` / `test_e2e_docx.py` остаются зелёными без правок — приёмка FR-003/FR-004).

### 10.3 E2E real_model (`make test-real` / `make pre-release`)

`tests/integration/test_m2m_real_model.py`, маркер `real_model` (по образцу `test_pipeline_real_model.py`, subprocess-режим OPF): полный M2M-флоу с ключом на документах из `C:\MyProjects\MyTasks\summerizer\Договоры` (`ф2.docx`, `ф3.cleaned.md`; при необходимости `ф4в1.cleaned.md`): upload → poll до `completed` → бинарный download + JSON с base64; оба результата содержат плейсхолдеры `<PRIVATE_PERSON1>`-стиля и не содержат исходных PII из источников. Запуск — `NEIRONIR_RUN_REAL_MODEL_TESTS=1`.

Coverage ≥ 70% сохраняется (P-009).

## 11. Противоречия с requirements.md (обнаружены, требования НЕ переписаны)

1. **FR-003 исходит из предпосылки**, что `/api/v1/documents*` защищён session-аутентификацией. Фактически в коде (`api/jobs.py`, `frontend/app.js`, тесты `test_api.py`, `test_e2e_md.py`) documents-эндпоинты **не требуют ни session, ни CSRF** — UI-флоу работает без логина. Дизайн сохраняет фактическое поведение («accept as before» = принять без проверки session): требование формально выполняется, но «валидная session» на documents-эндпоинтах не проверяется ни до, ни после фичи. Следствие — риск R-1.
2. **Q-002 из requirements** сформулирован в терминах «валидной сессии» на documents-эндпоинтах; в дизайне Q2 сведён к чистому правилу «Authorization-заголовок присутствует → только API-key путь, иначе pass-through» — детерминирован и тестируем, семантика сохранена.

## 12. Technical Decisions

### TD-001 (Q1): 401 + `WWW-Authenticate: Bearer` для любого невалидного Bearer; 403 — зарезервирован
- **Why:** единый код не раскрывает, «известен ли ключ формату»; 403 пригодится для per-key прав в будущем. Клиент ретраит 401 только сменой ключа.
- **Trade-off:** клиент не отличает «ключ отозван» от «ключа нет в списке» — приемлемо для статических ключей.

### TD-002 (Q2): Bearer-приоритет без fallback
- **Why:** детерминированность; невалидный Bearer при валидной сессии → 401 (утверждено пользователем).
- **Trade-off:** сломанный M2M-клиент с «залипшим» заголовком не спасётся чужой сессией — это правильно (P-014).

### TD-003 (Q3): контент-неготиация на существующем `/download`, а не новый эндпоинт
- **Why:** один URL на job, обратная совместимость автоматична (без Accept → бинарный), меньше OpenAPI-поверхности.
- **Trade-off:** семантика одного URL зависит от Accept; упрощённый точный-match парсер (не полный RFC 7231 с q-значениями) — `*/*` даёт бинарный файл.

### TD-004 (Q4): CSRF не применяется к Bearer-запросам
- **Why:** CSRF-атака строится на cookie-аутентификации; Bearer не отправляется браузером автоматически. На documents-роутере `verify_csrf` отсутствует и не добавляется.
- **Trade-off:** если в будущем documents получат cookie-auth, Bearer-запросы надо будет явно исключать из CSRF-проверки (задел — `request.state.auth_via`).

### TD-005 (Q5): `NEIRONIR_API_KEYS` — одна строка, ключи через запятую
- **Why:** pydantic-settings не любит list[str] из одной env-переменной без JSON-синтаксиса; запятая — простейший формат. Пусто → множество пусто → M2M отключён (NFR-004).
- **Trade-off:** ключ с запятой внутри невозможен (недопустимо по формату).

### TD-006: dependency на router, не middleware и не per-endpoint
- **Why:** (а) middleware в `main.py` сложно переопределять в тестах, dependency — стандартно через `app.dependency_overrides`; (б) per-endpoint-набор разросся бы в матрицу и оставил бы feedback-эндпоинты дырой для невалидных ключей (FR-002 требует reject на всём `/documents*`). M2M-контракт при этом документационно ограничен upload/get_job/download.
- **Trade-off:** валидный ключ технически проходит и на feedback-эндпоинты — осознанно: это доверенные внутренние системы, а фича не вводит per-key права.

### TD-007: запросы без Authorization-заголовка проходят без проверки (pass-through)
- **Why:** единственный вариант, при котором (а) выполняется FR-003 «accept as before», (б) не падает ни один существующий e2e/integration-тест (они не отправляют заголовков), (в) UI работает без логина, как сегодня, независимо от `NEIRONIR_API_KEYS` (NFR-004).
- **Trade-off:** ключи работают как идентификация M2M, а не как замок (R-1). Закрытие documents-эндпоинтов для заголовко-отсутствующих запросов — отдельная будущая фича (потребует gating UI за логином).

## 13. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| R-1: без Authorization-заголовка эндпоинты остаются открытыми — API-ключ не ограничивает доступ, а только идентифицирует M2M | Medium (локальный single-user сервис; требования не требуют обратного) | Явно задокументировать в docs/api.md; TD-007; hardening — будущая фича (см. §11.1) |
| R-2: JSON/base64 раздувает ответ на ~33% (20 МБ файл → ~27 МБ JSON) | Low | M2M-клиенты обычно берут небольшие документы; бинарный download остаётся основным; лимит `max_file_size` действует на upload |
| R-3: утравленный логируемый ключ (в env, в коммит, в баг-репорт) | High | `.env` в `.gitignore`; ключи не логируются; тест проверяет отсутствие ключа в теле 401 |
| R-4: timing-атака на сравнение ключей | Low | `secrets.compare_digest` по каждому ключу (§3.1) |
| R-5: поломка обратной совместимости download | Medium | Регресс-тесты: без Accept → бинарный (§10.2); существующие тесты не правятся |

## 14. Implementation FAQ

**Q:** Почему 401, а не 403, для ключа «правильного формата, но не из списка»?  
**A:** TD-001/Q1 — единый 401 не даёт злоумышленнику информации о валидности формата; 403 зарезервирован под будущие per-key права.

**Q:** Что если клиент шлёт и cookie, и Bearer?  
**A:** Всегда выигрывает Bearer (Q2): валидный → обработка, невалидный → 401. Cookie в это время не читается.

**Q:** Как отключить M2M?  
**A:** `NEIRONIR_API_KEYS=` (пусто). Bearer-запросы получают 401, UI работает как раньше.

**Q:** Почему `Accept: */*` даёт бинарный файл, а не JSON?  
**A:** Обратная совместимость (Q3): старые клиенты и браузеры без явного Accept должны получать ровно прежний ответ; JSON — только явный `Accept: application/json`.

**Q:** Нужен ли CSRF-токен для POST upload с Bearer?  
**A:** Нет (Q4): CSRF применим к cookie-auth; documents-роутер CSRF и сейчас не проверяет.

**Q:** Применимы ли Bearer-ключи к admin/rules/training?  
**A:** Нет — `require_documents_auth` висит только на `jobs.router` (NFR-002, Out of Scope).

**Q:** Может ли M2M-клиент слать feedback?  
**A:** Технически валидный ключ пройдёт, но feedback/annotations/apply-feedback исключены из M2M-контракта (не документируются, могут меняться) — TD-006.

**Q:** Где живёт логика «ключи через запятую»?  
**A:** `auth/api_key.py::parse_api_keys`; `Settings.api_keys` — сырая строка, `Settings.api_key_set` — property-множество. Обновление ключей — рестарт процесса (как и весь Settings, lru_cache).