# API

Базовый префикс: `/api/v1`. Формат: JSON (кроме upload/download).

## POST `/api/v1/documents`

Загрузить документ на обработку.

**Content-Type:** `multipart/form-data`

**Параметры формы:**

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `file` | file | да | Файл `.md` или `.docx` |

**Ответ `202 Accepted`:**

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "pending",
  "source_filename": "Договор.docx",
  "source_ext": "docx",
  "created_at": "2025-07-27T10:15:23.123Z",
  "finished_at": null,
  "error": null,
  "access_token": "vN2p...-opaque-random-token"
}
```

**`access_token`** — одноразовый opaque job access token (spec 007 T3). Выдаётся
ровно один раз, в ответе на создание задачи, и нигде больше не повторляется.
В `job.json` на сервере хранится только SHA-256 digest токена, сам токен не
хранится и не логируется. Клиент обязан сохранить токен и передавать его в
заголовке `X-Job-Token` на все запросы к конкретной задаче (статус, download,
annotations, feedback, apply-feedback, preview), иначе ответ `404`.

## Job access token (`X-Job-Token`)

**Ошибки:**

| Код | Когда |
|---|---|
| `400` | Неподдерживаемое расширение |
| `413` | Файл больше `MAX_FILE_SIZE` (по умолчанию 20 МБ) |

Все job-scoped запросы (`GET /{job_id}`, `/download`, `/annotations`,
`/feedback`, `/apply-feedback`, `/preview`) требуют заголовок
`X-Job-Token: <токен из ответа на загрузку>` или валидную сессию администратора
(явный bypass). Неверный токен, отсутствующий токен и legacy-задача без digest
(создана до ввода токенов) для non-admin дают **одинаковый `404`**
`job_not_found` — API не раскрывает, существует ли UUID.

- Токен — opaque случайная строка (`secrets.token_urlsafe`); повторный запрос
  с тем же токеном остаётся валидным до удаления задачи.
- Потерянный токен не восстанавливается: единственный путь — пере загрузить
  документ (admin может открыть legacy/забытую задачу через admin-сессию).
- Токен принимается **только из заголовка** `X-Job-Token` (spec 008): токен в
  query-параметре `?access_token=...` не авторизует запрос (тот же `404`,
  что и при отсутствии токена) — токен задачи никогда не появляется в URL,
  истории браузера и access-логах.
- Скачивание из браузерного UI — запросом из сценария: `fetch()` с заголовком
  `X-Job-Token`, файл сохраняется через `Blob`/`URL.createObjectURL`.
  Прямая навигация `<a href>` на `/download` без заголовка не сработает.
- Приоритет Bearer (M2M) сохраняется: невалидный `Authorization` даёт `401`
  даже при валидном `X-Job-Token`.

## GET `/api/v1/documents/{job_id}`

Получить статус задачи.

Требует `X-Job-Token` (см. выше).

**Ответ `200 OK`:** объект `Job` (как выше, **без** поля `access_token`).

**Статусы:**

| Статус | Значение |
|---|---|
| `pending` | Принят, ещё не начат |
| `processing` | Privacy-filter работает |
| `completed` | Готово, файл доступен для скачивания |
| `failed` | Ошибка (см. поле `error`) |

**Ошибки:**

| Код | Когда |
|---|---|
| `404` | `job_id` не найден |

## GET `/api/v1/documents/{job_id}/download`

Скачать очищенный файл.

- Формат файла: **тот же**, что и на входе (`.md` → `.md`, `.docx` → `.docx`).
- Имя файла в `Content-Disposition`: `<original-name>.cleaned.<ext>`.
  - Пример: `Договор.docx` → `Договор.cleaned.docx`.

**Ошибки:**

| Код | Когда |
|---|---|
| `404` | Задача не найдена |
| `409` | Задача ещё не завершена или завершилась с ошибкой |

По умолчанию отдаётся бинарный файл. JSON-выдача с base64-контентом — при
`Accept: application/json`, см. раздел [Machine-to-machine API](#machine-to-machine-api).

## Machine-to-machine API

Программный (M2M) доступ к пайплайну — по статическим Bearer-ключам. Эндпоинты
те же, что у веб-интерфейса; отдельного префикса и OAuth нет.

### Настройка

Ключи задаются переменной `NEIRONIR_API_KEYS` — через запятую:

```
NEIRONIR_API_KEYS=key-prod-1, key-prod-2
```

- Пробелы вокруг ключей отбрасываются, пустые элементы списка игнорируются.
- **Пустое значение = M2M-доступ отключён**: запросы с `Authorization` получают
  `401`, веб-интерфейс работает как раньше.
- Ключи читаются при старте процесса; смена списка — рестарт.
- Значения ключей **не логируются** и не попадают в сообщения об ошибках;
  сравнение — в constant-time (`secrets.compare_digest`).

### Правила доступа

Два независимых уровня: (1) Bearer-граница для `Authorization` (ниже) и
(2) per-job `X-Job-Token` на всех job-scoped маршрутах (см. [Job access token](#job-access-token-x-job-token)).
Для M2M-клиента оба уровня обязательны на follow-up запросах: токен берётся
из поля `access_token` в ответе `202` на upload.

Поведение запросов к `/api/v1/documents*` определяется наличием заголовка
`Authorization`:

- **Заголовок есть** — только путь API-ключа. Схема обязана быть
  `Bearer <ключ>`; иначе (другая схема, пустой токен, ключ не из списка,
  ключи не настроены) — `401` `invalid_api_key` + `WWW-Authenticate: Bearer`.
  Session-cookie при этом не проверяется — тихого fallback на сессию нет:
  невалидный Bearer получает `401`, даже если приложена валидная cookie
  (приоритет Bearer над cookie).
- **Заголовка нет** — пропускается только от loopback-клиента (адрес
  `127.0.0.1` / `::1`, локальный браузерный UI — spec 007 FR-001/FR-002):
  запрос обрабатывается без проверки ключа. Non-loopback-клиент без
  заголовка получает `401` `invalid_api_key`; неопределимый адрес клиента
  трактуется как non-loopback (fail-closed). То есть ключи идентифицируют
  M2M-клиента, а анонимный доступ ограничен самой машиной.

`403` не используется (зарезервирован под будущие per-key права).

### Контракт M2M

| Метод и путь | Успех | Коды ошибок |
|---|---|---|
| `POST /api/v1/documents/` | `202` — `JobResponse` | `400`, `401`, `413` |
| `GET /api/v1/documents/{job_id}` | `200` — `JobResponse` | `401`, `404` |
| `GET /api/v1/documents/{job_id}/download` | `200` — бинарный файл или JSON | `401`, `404`, `409`, `413` |

У `POST` важен завершающий слэш (`/api/v1/documents/`) — без него FastAPI
вернёт `307`-редирект. Остальные эндпоинты под `/api/v1/documents/{job_id}`
(annotations, preview, feedback, apply-feedback) проверяют Bearer-ключ тем же
guard'ом, но **вне M2M-контракта**: для машин они не документируются и могут
меняться без уведомления.

Тело ошибки — конверт `{"detail": {"code": "...", "message": "..."}}`:

| HTTP | `code` | Когда |
|---|---|---|
| `400` | `unsupported_format` | Файл не `.md`/`.docx` |
| `400` | `unsupported_output_format` | `output_format` неизвестен или несовместим с источником |
| `401` | `invalid_api_key` | Присутствующий `Authorization` не прошёл проверку; ответ содержит `WWW-Authenticate: Bearer` |
| `404` | `job_not_found` | Задача не найдена, `X-Job-Token` неверен/отсутствует, либо legacy-задача без digest (для non-admin) — ответы неразличимы |
| `409` | `job_not_ready` | Download при статусе задачи ≠ `completed` |
| `413` | `file_too_large` | Файл больше `NEIRONIR_MAX_FILE_SIZE` |
| `413` | `result_too_large` | JSON-выдача: файл результата больше `NEIRONIR_MAX_JSON_DOWNLOAD_SIZE` (бинарная выдача не ограничена) |

Невалидный `job_id` (не UUID) — стандартный `422` FastAPI.

### Флоу: upload → poll → download

```bash
KEY="my-secret-key"
BASE="http://127.0.0.1:8000/api/v1/documents"

# 1. Загрузка (multipart). Опционально output_format=md|docx.
curl -s -X POST "$BASE/" \
  -H "Authorization: Bearer $KEY" \
  -F "file=@Договор.docx" \
  -F "output_format=md"
# → 202 {"id": "…", "status": "pending", "access_token": "…", …}
# access_token возвращается только здесь; в ответе хранится только его digest.

JOB_ID="<id из ответа>"
JOB_TOKEN="<access_token из ответа>"   # одноразовый, вернуть его нельзя

# 2. Опрос статуса: pending → processing → completed | failed
#    Рекомендуемый интервал опроса — не менее 2 секунд; обработка может
#    занимать до NEIRONIR_PRIVACY_FILTER_TIMEOUT (по умолчанию 600 с).
curl -s -H "Authorization: Bearer $KEY" -H "X-Job-Token: $JOB_TOKEN" "$BASE/$JOB_ID"
# failed — терминальный статус, причина в поле error.

# 3a. Скачать бинарный файл (по умолчанию — без Accept или с Accept: */*)
curl -s -OJ -H "Authorization: Bearer $KEY" -H "X-Job-Token: $JOB_TOKEN" \
  "$BASE/$JOB_ID/download"

# 3b. Скачать JSON с base64-контентом
curl -s -H "Authorization: Bearer $KEY" -H "X-Job-Token: $JOB_TOKEN" \
  -H "Accept: application/json" "$BASE/$JOB_ID/download"
```

### Выдача результата: два формата

Формат ответа `GET .../download` определяется заголовком `Accept`:

- точный media-range `application/json` (параметры вида `; q=0.9` и другие
  элементы списка игнорируются) → **JSON**;
- всё остальное — отсутствие заголовка, `*/*`, `application/jsonx` и т.п. —
  **бинарный файл**, как раньше (обратная совместимость со старыми клиентами).

Бинарный ответ: `Content-Disposition` с именем `<имя-источника>.cleaned.<ext>`
и MIME по расширению (`text/markdown; charset=utf-8` для `.md`, официальный
Office-тип для `.docx`). Для не-ASCII имён (например, кириллицы) заголовок
содержит два параметра: ASCII-fallback в `filename="..."` (не-ASCII-символы
заменяются на `_`) и оригинальное имя в `filename*=utf-8''...` (RFC 5987/
6266 §4.3) — упрощённые M2M-клиенты читают первый, корректные — второй.
Пример: `ф2.docx` → `filename="_2.cleaned.docx"; filename*=utf-8''%D1%842.cleaned.docx`.

JSON-ответ — модель `DownloadResultResponse`:

```json
{
  "job_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "filename": "Договор.cleaned.md",
  "ext": "md",
  "media_type": "text/markdown; charset=utf-8",
  "size": 15234,
  "content_base64": "…"
}
```

`content_base64` — base64 того же файла, что отдаётся в бинарной ветке;
JSON-ответ примерно на треть больше самого файла.

JSON-ветка ограничена по размеру (spec 007 T6): если файл результата больше
`NEIRONIR_MAX_JSON_DOWNLOAD_SIZE` (по умолчанию 5 МиБ), ответ — `413`
`result_too_large`. Проверка идёт по размеру файла на диске ещё до чтения
в память, так что объём ответа и потребление памяти ограничены. Бинарная
ветка этим лимитом не ограничена — забирайте файл без `Accept: application/json`.

## API правил (`/api/v1/rules`)

Управление детектирующими правилами, накопленными из правок пользователей.
**Админский API**: каждый эндпоинт требует валидную сессию администратора
(cookie `neironir_session`), а не-GET — ещё и совпадающий CSRF-токен.
Ключи M2M и `X-Job-Token` здесь не применяются.

Жизненный цикл правила: анализ накопленных правок предлагает правила
(`proposed`) → администратор утверждает (`approved` — правило компилируется
и подключается к детектору на лету) или отклоняет (`rejected`).

| Метод и путь | Назначение |
|---|---|
| `GET /api/v1/rules` | Список всех правил: встроенные + предложенные + утверждённые + отклонённые |
| `GET /api/v1/rules/stats` | Агрегированная статистика правок (задачи с фидбеком, пропуски по типам, ложные срабатывания) |
| `POST /api/v1/rules/proposals?min_occurrences=3` | Запустить анализ правок и сохранить новые предложения (авто-утверждения нет) |
| `POST /api/v1/rules/{rule_id}/approve` | Утвердить предложение; `404` — правило не найдено |
| `POST /api/v1/rules/{rule_id}/reject` | Отклонить предложение; `404` — правило не найдено |
| `POST /api/v1/rules` | Добавить ручное правило (сразу `approved`, hot-reload) |

Тело `POST /api/v1/rules` — JSON (не query-параметры, чтобы длинные паттерны
не попадали в журнал доступа): `entity_type` (один из 8 типов), `pattern`
(строка ≥ 3 символов), `description` (опционально).

### Ошибки валидации (`422`)

| Случай | Ответ |
|---|---|
| Неизвестный `entity_type` | Текстовая ошибка со списком допустимых типов |
| `pattern` короче 3 символов | Текстовая ошибка |
| Опасный паттерн (ReDoS-guard, spec 007 T5) | `{"detail": {"code": "unsafe_regex", "reason": "...", "message": "..."}}` |
| Синтаксически невалидный regex | Текстовая ошибка с причиной `re.compile` |

Контракт `unsafe_regex`: ReDoS-guard отклоняет вложенные квантификаторы
(например, `(a+)+$`), обратные ссылки (`\1`…`\9`, `\g<...>`) и паттерны
длиннее 200 символов — **до** компиляции и исполнения. Стабильный код
`unsafe_regex` позволяет клиенту отличить «опасный паттерн» от
«синтаксически неверного». Сохранённые правила, ставшие опасными,
при загрузке детектора пропускаются — в журнал пишется только идентификатор
правила и причина, сам паттерн и текст документов не логируются.

## Корневой `/`

Отдаёт SPA (`frontend/index.html`). Никаких эндпоинтов авторизации нет.

## Ограничения

- **Размер файла:** `MAX_FILE_SIZE` из конфига (по умолчанию 20 МБ).
- **Параллельность:** сколько угодно задач, обработка — последовательная в одном процессе (см. [architecture.md](./architecture.md)).
- **TTL файлов:** не очищаются автоматически. Уборка — ручная (отдельный скрипт/команда) или системный cron.

## Конфигурация

Через переменные окружения (`.env` или процесс), префикс `NEIRONIR_`:

| Имя | Дефолт | Описание |
|---|---|---|
| `NEIRONIR_HOST` | `127.0.0.1` | Адрес uvicorn |
| `NEIRONIR_PORT` | `8000` | Порт uvicorn |
| `NEIRONIR_STORAGE_DIR` | `./storage` | Корень файлового хранилища |
| `NEIRONIR_MAX_FILE_SIZE` | `20971520` (20 МБ) | Лимит загрузки в байтах |
| `NEIRONIR_MAX_JSON_DOWNLOAD_SIZE` | `5242880` (5 МиБ) | Лимит JSON-выдачи результата (base64); бинарная выдача не ограничена (см. [Выдача результата](#выдача-результата-два-формата)) |
| `NEIRONIR_PRIVACY_FILTER_MODE` | `mock` | `mock` — regex-эвристики (без модели); `subprocess` — вызов `opf` CLI |
| `NEIRONIR_PRIVACY_FILTER_CMD` | `python -m opf` | Команда запуска CLI privacy-filter (для `subprocess`) |
| `NEIRONIR_PRIVACY_FILTER_TIMEOUT` | `600` | Таймаут обработки, секунды |
| `NEIRONIR_PRIVACY_FILTER_DEVICE` | `cpu` | Устройство (`cpu` или `cuda`) |
| `NEIRONIR_PRIVACY_FILTER_CHECKPOINT_DIR` | _пусто_ | Путь к чекпойнту; пусто — opf использует `OPF_CHECKPOINT` или `~/.opf/privacy_filter` |
| `NEIRONIR_FRONTEND_DIR` | `frontend` | Каталог статического фронтенда |
| `NEIRONIR_API_KEYS` | _пусто_ | Статические Bearer-ключи M2M-доступа, через запятую; пусто — M2M отключён (см. [Machine-to-machine API](#machine-to-machine-api)) |
| `NEIRONIR_LOG_LEVEL` | `INFO` | Уровень логирования |

Полный список — в `backend/neironir/config.py` (один источник истины).
См. также [`.env.example`](../.env.example) в корне репозитория.
