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
│       │   ├── jobs.py        # /api/v1/documents
│       │   └── ui.py          # GET / для отдачи SPA
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
    "error": null
  }
  ```
- БД не используем. Карту замен не храним (по решению).

## Ограничения MVP

- Один пользователь, без авторизации.
- Без очередей (Celery/RQ) — обработка в `BackgroundTasks` FastAPI внутри того же процесса.
- Без Docker. Без CI-обвязки уровня prod. Линт/тесты — локально или в GitHub Actions по желанию.
- Поддержка форматов: **только `.md` и `.docx`**. Никаких PDF, RTF, изображений.

## Что выходит за рамки MVP

- Возврат исходных данных (reverse map).
- Поддержка других форматов.
- Авторизация и роли.
- Хранение истории загрузок дольше сессии.
- Горизонтальное масштабирование, очереди, брокеры.
