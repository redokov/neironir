# Архитектура

## Назначение

`neironir` — локальный веб-сервис. Пользователь загружает документ (`.md` или `.docx`), сервис через модель [privacy-filter](https://github.com/openai/privacy-filter.git) заменяет в нём персональные данные на плейсхолдеры, отдаёт очищенный файл того же формата. **Обратной замены нет** — преобразование однонаправленное.

## Типы сущностей и плейсхолдеры

Список из 8 типов — источник истины для всего проекта (enum `EntityType` в backend):

| Тип в privacy-filter | Плейсхолдер (шаблон) | Пример |
|---|---|---|
| `private_person` | `<PRIVATE_PERSON{n}>` | `<PRIVATE_PERSON1>`, `<PRIVATE_PERSON2>` |
| `private_address` | `<PRIVATE_ADDRESS{n}>` | `<PRIVATE_ADDRESS1>` |
| `private_email` | `<PRIVATE_EMAIL{n}>` | `<PRIVATE_EMAIL1>` |
| `private_phone` | `<PRIVATE_PHONE{n}>` | `<PRIVATE_PHONE1>` |
| `private_date` | `<PRIVATE_DATE{n}>` | `<PRIVATE_DATE1>` |
| `private_url` | `<PRIVATE_URL{n}>` | `<PRIVATE_URL1>` |
| `account_number` | `<ACCOUNT_NUMBER{n}>` | `<ACCOUNT_NUMBER1>` |
| `secret` | `<SECRET{n}>` | `<SECRET1>` |

`{n}` — счётчик вхождений данного типа в пределах **одного документа**, начинается с 1. Нумерация сквозная по документу, не по всему сервису: каждый новый документ начинает счёт заново.

> Имена типов в коде и в API — `snake_case` (как в privacy-filter). Имена плейсхолдеров — `UPPER_SNAKE_CASE` с `<>`.

## Поток данных

```
┌────────┐  upload (multipart)  ┌────────────┐  сохраняем во временный   ┌────────────┐
│ Browser├──────────────────────►│  FastAPI   │  файл + создаём Job      │  storage/  │
└───┬────┘                       │   (API)    ├──────────────────────────►│  jobs/{id} │
    │                            └──┬─────────┘                           └────┬──────┘
    │                               │                                            │
    │ poll GET /jobs/{id}           │ BackgroundTasks                            │
    │ ◄─────────────────────────────┤                                            │
    │                               │                                            ▼
    │                               │                                    ┌────────────────┐
    │                               │ extract text (.md / .docx)         │  PrivacyFilter │
    │                               ├───────────────────────────────────►│  (subprocess)  │
    │                               │                                    └───────┬────────┘
    │                               │ ◄──────────── annotated text ────────────┘
    │                               │ build doc (.md / .docx) with placeholders
    │                               │ write result file
    │                               ▼
    │  GET /jobs/{id}/download ───► отдаём очищенный файл
    ▼
```

Подробный пошаговый разбор — в [agents/03-backend.md](./agents/03-backend.md).

## Структура каталогов

```
neironir/
├── backend/
│   └── neironir/
│       ├── __init__.py
│       ├── main.py            # FastAPI app factory
│       ├── config.py          # pydantic-settings
│       ├── api/
│       │   ├── __init__.py
│       │   ├── auth.py        # admin login/logout (сессии)
│       │   ├── dependencies.py # dependency-фабрики
│       │   ├── jobs.py        # /api/v1/documents
│       │   ├── rules.py       # /api/v1/rules (proposals, unsafe_regex)
│       │   ├── schemas.py     # request/response-модели
│       │   └── ui.py          # GET / для отдачи SPA
│       ├── admin/             # админ-панель: логин, правила, статистика
│       ├── auth/              # сессии, CSRF, API-ключи, max-body-size (spec 007)
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── entity_type.py # EntityType enum, шаблоны
│       │   ├── job.py         # Job, JobStatus
│       │   └── placeholder.py # логика нумерации
│       ├── converters/
│       │   ├── __init__.py
│       │   ├── base.py        # DocumentConverter interface
│       │   ├── markdown.py    # извлечение и сборка md
│       │   └── docx.py        # извлечение и сборка docx
│       ├── privacy/
│       │   ├── __init__.py
│       │   └── client.py      # PrivacyFilterClient (subprocess)
│       ├── storage/
│       │   ├── __init__.py
│       │   └── local.py       # локальная FS-реализация
│       └── workers/
│           ├── __init__.py
│           └── pipeline.py    # run_job(jod_id) — оркестратор
├── frontend/
│   ├── index.html             # SPA-страница
│   ├── app.js
│   └── styles.css
├── privacy-filter/            # git submodule
├── tests/
│   ├── unit/
│   └── integration/
├── docs/                      # эта документация
├── pyproject.toml             # uv/poetry
├── uv.lock
├── .gitignore
├── .python-version
└── README.md
```

## Хранилище

- `storage/jobs/{job_id}/source.{ext}` — загруженный файл.
- `storage/jobs/{job_id}/result.{ext}` — очищенный файл.
- Состояние `Job` — JSON в `storage/jobs/{job_id}/job.json`:
  ```json
  {
    "id": "uuid4",
    "status": "pending|processing|completed|failed",
    "source_filename": "Договор.docx",
    "source_ext": "docx",
    "created_at": "2025-...",
    "finished_at": null,
    "error": null,
    "access_token_digest": "<sha256-hex digest> | null"
  }
  ```
- БД не используем. Карту замен не храним (по решению). Самого job-токена
  в хранилище нет — только его SHA-256 digest (spec 007 TD-003).

## Авторизация и безопасность (обзор)

- **Job-token (`X-Job-Token`).** Opaque `access_token` выдаётся ровно один
  раз в ответе `202` на upload; в `job.json` хранится только SHA-256 digest.
  Все job-scoped эндпоинты (статус, download, annotations, feedback,
  preview) проверяют заголовок `X-Job-Token`; неверный/отсутствующий токен и
  legacy-задача без digest для non-admin дают неразличимый `404`
  `job_not_found` — API не раскрывает существование задачи.
- **Граница авторизации.** Запрос без `Authorization` пропускается только от
  loopback-клиента (локальный браузерный UI); non-loopback без валидного
  Bearer-ключа получает `401`. Присланный невалидный Bearer — всегда `401`,
  даже при валидной session-cookie (приоритет Bearer над сессией).
- **JSON download cap.** JSON-ветка download (base64-контент) проверяется по
  размеру файла до чтения в память: сверх `NEIRONIR_MAX_JSON_DOWNLOAD_SIZE`
  (5 МиБ по умолчанию) — `413`; бинарная ветка капом не ограничена.
- **Rules API.** Небезопасные regex-паттерны в правилах отклоняются
  стабильным кодом ошибки `unsafe_regex` — отличимым от синтаксически
  невалидных паттернов.

Детали и примеры — в [api.md](./api.md).

## Ограничения MVP

- Один пользователь; авторизация MVP — loopback-граница + per-job `X-Job-Token`
  + admin-сессия, без ролей и per-key прав (`403` зарезервирован под них).
- Без очередей (Celery/RQ) — обработка в `BackgroundTasks` FastAPI внутри того же процесса.
- Без Docker. Без CI-обвязки уровня prod. Линт/тесты — локально или в GitHub Actions по желанию.
- Поддержка форматов: **только `.md` и `.docx`**. Никаких PDF, RTF, изображений.
- `.docx`-конвертер сохраняет только параграфы, форматирование/таблицы/списки игнорируются. `Replacement`, пересекающий границу параграфов, выбрасывает `ValueError`.

## Что выходит за рамки MVP

- Возврат исходных данных (reverse map).
- Поддержка других форматов.
- Роли и per-key права (широкая авторизация уже есть: job-токены, admin-сессии, M2M-ключи).
- Хранение истории загрузок дольше сессии.
- Горизонтальное масштабирование, очереди, брокеры.
