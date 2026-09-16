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
  "error": null
}
```

**Ошибки:**

| Код | Когда |
|---|---|
| `400` | Неподдерживаемое расширение |
| `413` | Файл больше `MAX_FILE_SIZE` (по умолчанию 20 МБ) |

## GET `/api/v1/documents/{job_id}`

Получить статус задачи.

**Ответ `200 OK`:** объект `Job` (как выше).

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

Поведение запросов к `/api/v1/documents*` определяется наличием заголовка
`Authorization`:

- **Заголовок есть** — только путь API-ключа. Схема обязана быть
  `Bearer <ключ>`; иначе (другая схема, пустой токен, ключ не из списка,
  ключи не настроены) — `401` `invalid_api_key` + `WWW-Authenticate: Bearer`.
  Session-cookie при этом не проверяется — тихого fallback на сессию нет:
  невалидный Bearer получает `401`, даже если приложена валидная cookie
  (приоритет Bearer над cookie).
- **Заголовка нет** — запрос проходит без проверки авторизации (прежнее
  поведение веб-интерфейса). То есть ключи идентифицируют M2M-клиента,
  но не «запирают» эндпоинты: запрос без заголовка обработается как раньше.

`403` не используется (зарезервирован под будущие per-key права).

### Контракт M2M

| Метод и путь | Успех | Коды ошибок |
|---|---|---|
| `POST /api/v1/documents/` | `202` — `JobResponse` | `400`, `401`, `413` |
| `GET /api/v1/documents/{job_id}` | `200` — `JobResponse` | `401`, `404` |
| `GET /api/v1/documents/{job_id}/download` | `200` — бинарный файл или JSON | `401`, `404`, `409` |

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
| `404` | `job_not_found` | Задача не найдена |
| `409` | `job_not_ready` | Download при статусе задачи ≠ `completed` |
| `413` | `file_too_large` | Файл больше `NEIRONIR_MAX_FILE_SIZE` |

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
# → 202 {"id": "…", "status": "pending", …}

JOB_ID="<id из ответа>"

# 2. Опрос статуса: pending → processing → completed | failed
#    Рекомендуемый интервал опроса — не менее 2 секунд; обработка может
#    занимать до NEIRONIR_PRIVACY_FILTER_TIMEOUT (по умолчанию 600 с).
curl -s -H "Authorization: Bearer $KEY" "$BASE/$JOB_ID"
# failed — терминальный статус, причина в поле error.

# 3a. Скачать бинарный файл (по умолчанию — без Accept или с Accept: */*)
curl -s -OJ -H "Authorization: Bearer $KEY" "$BASE/$JOB_ID/download"

# 3b. Скачать JSON с base64-контентом
curl -s -H "Authorization: Bearer $KEY" -H "Accept: application/json" \
  "$BASE/$JOB_ID/download"
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
