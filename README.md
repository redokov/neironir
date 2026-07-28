# neironir

Веб-сервис для удаления персональных данных из текстовых документов (`.md`, `.docx`) перед загрузкой во внешние нейросети.

## Что это

Пользователь загружает документ в сервис. Сервис через локально развёрнутую модель [privacy-filter](https://github.com/openai/privacy-filter.git) заменяет в тексте сущности восьми типов на шаблоны:

| Тип сущности | Шаблон |
|---|---|
| Имя (private_person) | `<PRIVATE_PERSON1>` |
| Адрес (private_address) | `<PRIVATE_ADDRESS1>` |
| Email (private_email) | `<PRIVATE_EMAIL1>` |
| Телефон (private_phone) | `<PRIVATE_PHONE1>` |
| Дата (private_date) | `<PRIVATE_DATE1>` |
| URL (private_url) | `<PRIVATE_URL1>` |
| Номер счёта (account_number) | `<ACCOUNT_NUMBER1>` |
| Секрет/пароль (secret) | `<SECRET1>` |

Пользователь получает очищенный документ того же формата. **Обратной замены нет** — преобразование однонаправленное.

## Стек

- **Backend:** Python 3.11+, FastAPI, Pydantic v2, `python-docx`, `uv`.
- **ML:** [privacy-filter](https://github.com/openai/privacy-filter.git) — вызывается как CLI-subprocess (режим `subprocess`) или заменяется regex-эвристикой (режим `mock`).
- **Frontend:** vanilla HTML/CSS/JS, без сборщиков и фреймворков.
- **Storage:** локальная файловая система (для MVP).

## Поддерживаемые форматы

- Markdown (`.md`) — полная поддержка.
- Microsoft Word (`.docx`) — поддержка параграфов; форматирование, таблицы, списки, изображения игнорируются (см. [docs/architecture.md](./docs/architecture.md#ограничения-mvp)).

## Структура

```
neironir/
├── backend/neironir/         # FastAPI-приложение
│   ├── api/                  # роутеры, схемы, dependency-фабрики
│   ├── converters/           # .md / .docx ↔ текст
│   ├── domain/               # EntityType, Job, PlaceholderCounter
│   ├── privacy/              # адаптер к privacy-filter (subprocess / mock)
│   ├── storage/              # локальное хранилище задач
│   └── workers/              # run_job() — пайплайн обработки
├── frontend/                 # SPA: drop-zone, polling, download
├── tests/                    # unit + integration + e2e
├── docs/                     # архитектура, API, quickstart, агенты
├── .github/workflows/        # CI (lint + type + test, coverage-gate 70%)
├── pyproject.toml            # uv/PEP 621
├── Makefile                  # install / test / lint / type / run
├── .env.example              # шаблон конфигурации
└── README.md
```

## Быстрый старт

```bash
# 1. Клонируем с подмодулями
git clone git@github.com:redokov/neironir.git
cd neironir
git submodule update --init --recursive

# 2. Устанавливаем зависимости
uv sync

# 3. Запускаем в mock-режиме (без модели)
make dev
# или напрямую:
uv run uvicorn neironir.main:app --reload --port 8000
```

Открыть http://127.0.0.1:8000/ — drop-zone, загрузить `.md`/`.docx`, дождаться «Готово», скачать очищенный файл.

Подключение реальной модели `opf` — в [docs/quickstart.md](./docs/quickstart.md#запуск-с-реальной-моделью-opf).

## Проверка

```bash
make check         # ruff + mypy + pytest
make test-cov      # coverage report
```

CI: GitHub Actions, см. `.github/workflows/ci.yml`.

## Документация

- [docs/acceptance-criteria.md](./docs/acceptance-criteria.md) — полный чек-лист для проверки (13 разделов: статика, тесты, mock, subprocess, ошибки, CI, ограничения).
- [docs/quickstart.md](./docs/quickstart.md) — установка, запуск, частые ошибки.
- [docs/api.md](./docs/api.md) — описание эндпоинтов (`/api/v1/documents`), коды ошибок, конфигурация.
- [docs/architecture.md](./docs/architecture.md) — поток данных, ограничения MVP.
- [docs/agents/](./docs/agents/) — спецификации фаз (0–5), включая [privacy-filter-research.md](./docs/agents/privacy-filter-research.md).

## Ограничения MVP

- Один пользователь, без авторизации.
- Обработка в `BackgroundTasks` FastAPI в одном процессе — без отдельной очереди/Celery.
- `.docx`-конвертер сохраняет только параграфы (без runs, таблиц, списков, изображений, сносок).
- Реальная модель (`opf`) подключается как subprocess; in-process Python API — за рамками.
- Без структурного логирования, метрик, горизонтального масштабирования.

Подробнее — в [docs/architecture.md](./docs/architecture.md#ограничения-mvp).

## Статус

Фазы 0–5 завершены: bootstrap, domain/API, backend (конвертеры, хранилище, privacy-client, pipeline, API), frontend (SPA), тесты (unit + integration + e2e), инфраструктура (Makefile, .env.example, CI, coverage). В режиме `mock` сервис полностью функционален. Режим `subprocess` готов, но требует установки `opf` (см. quickstart).
