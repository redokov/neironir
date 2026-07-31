# Conventions Steering

## Code Style

- **Python:** PEP 8 + ruff (`pyproject.toml` → `[tool.ruff]`).
- **Форматирование:** ruff format (не black, не yapf).
- **Импорты:** сортировка ruff (`I` rule).
- **Имена:**
  - Модули: `snake_case`.
  - Классы: `PascalCase` (Pydantic-модели, dataclasses).
  - Функции/методы/переменные: `snake_case`.
  - Константы: `UPPER_SNAKE_CASE`.
- **Типы:** обязательны для публичных API и Pydantic-моделей; внутренние хелперы — по усмотрению.
- **Имена типов в коде/JSON/API:** `snake_case` (как в privacy-filter: `private_person`, `account_number`).
- **Имена плейсхолдеров:** `UPPER_SNAKE_CASE` с `<>` (`<PRIVATE_PERSON1>`, `<ACCOUNT_NUMBER1>`).
- **Документация/комментарии:** на русском (проект локализован); идентификаторы, тесты, docstring API — на EN.
- **Docstring:** краткие, по делу; не дублировать очевидное имя метода.

## Architecture Patterns

- **Слои backend:**
  - `api/` — роутеры FastAPI, схемы запросов/ответов, dependency-фабрики.
  - `converters/` — `.md`/`.docx` ↔ текст; общий интерфейс `DocumentConverter` (см. `converters/base.py`).
  - `domain/` — `EntityType`, `Job`, `PlaceholderCounter` — без зависимостей от FastAPI/storage.
  - `privacy/` — адаптер к privacy-filter (`subprocess` / `mock`).
  - `storage/` — локальная FS-реализация.
  - `workers/` — `run_job()` — оркестратор пайплайна.
- **Принцип слоёв:** `domain` не импортирует ничего из `api`/`storage`/`privacy`/`converters`. `workers` координирует; `api` — тонкий слой маршрутизации.
- **Конфигурация:** через `pydantic-settings` (`backend/neironir/config.py`), читается из `.env`; см. `.env.example`.
- **Pydantic v2:** `BaseModel`, `Field`, `model_config = ConfigDict(...)` — без легаси-v1.
- **Логирование:** стандартный `logging` (без структурного логгера — см. ограничения MVP).

## Testing Rules

- **Фреймворк:** pytest.
- **Структура каталогов:**
  - `tests/unit/` — изолированные тесты (без сети, без subprocess).
  - `tests/integration/` — сценарии с реальной инфраструктурой (включая OPF-subprocess).
  - `tests/e2e/` — end-to-end (загрузка → очистка → скачивание).
- **Маркеры pytest:** `real_model` — для тестов, требующих реальной модели OPF.
- **Coverage:** gate ≥ 70% в CI.
- **Mock-режим по умолчанию:** все CI/unit-тесты прогоняются в mock; real_model — отдельный запуск.
- **Что тестируем:**
  - `domain/` — `EntityType`, `PlaceholderCounter` (сквозная нумерация).
  - `converters/` — извлечение/сборка `.md`/`.docx`, edge-кейсы (таблицы, пустой файл).
  - `privacy/client.py` mock — regex-паттерны для каждого типа.
  - `workers/pipeline.py` — оркестрация в mock-режиме.
  - API — эндпоинты `/api/v1/documents/*`, коды ошибок.
  - E2E — полный сценарий пользователя.

## Accessibility / Security Rules

- **Privacy-by-design:** карта замен **не хранится** нигде (БД нет, логи не пишут полный текст документа).
- **Локальность:** PII не покидают машину пользователя — никакой отправки в облачные API.
- **CSP/HTTPS:** в production — забота пользователя/оператора; в MVP — `http://127.0.0.1:8000/`.
- **Валидация входов:** через Pydantic-схемы API; не доверять `filename` от клиента.
- **Логи:** не логировать полный текст документа; только метаданные (длина, имя файла, тип, статус).
- **Ограничение размера файла:** см. `docs/api.md` (если не указано иное — разумный лимит на upload).
- **Accessibility UI:** базовая — labels, focus-states, контраст. Vanilla-JS SPA — без специальных ARIA-фреймворков.

## Workflow Rules

- **Ветки:** короткоживущие feature-ветки от `main`; PR с описанием и ссылкой на issue (если есть).
- **Коммиты:** осмысленные сообщения; привязка к фазе из `docs/agents/` или к issue.
- **CI:** все PR должны проходить `make check` (lint + type + test mock).
- **Pre-release:** `make pre-release` (включая `test-real`) обязателен перед релиз-тегом.
- **Документация:** изменения публичного поведения сопровождаются правкой `docs/` (api.md, architecture.md, README.md).
- **Privacy-filter submodule:** обновление — осознанно, с прогоном `make test-real`.
- **Согласования:** изменения в схеме API, типах `EntityType`, плейсхолдерах — только через явное обсуждение (влияет на обратную совместимость).
- **SDD (Spec-Driven Development):** при появлении новой фичи пройти `sdd-idea → sdd-prd → sdd-spec → sdd-tasks → sdd-exec → sdd-review` (см. `.ai/sdd/INDEX.md`).

## Dependency Conventions

- **Backend:** все runtime-зависимости — в `[project.dependencies]` `pyproject.toml`; dev — в `[project.optional-dependencies]` / `[dependency-groups]`.
- **Pinning:** `uv.lock` — источник истины для версий; ручной `pip install` запрещён.
- **Новые зависимости:** согласуются через PR с обоснованием в описании.

## Configuration Conventions

- **`.env.example`** — шаблон обязателен; реальный `.env` — в `.gitignore`.
- **Имена переменных:** `UPPER_SNAKE_CASE` с префиксом `NEIRONIR_` (например, `NEIRONIR_PRIVACY_MODE`).
- **Чувствительные значения:** не коммитить, не логировать.

## Open Questions

- **OQ-CV-001:** нужен ли обязательный `pre-commit` (ruff + mypy hooks)? Сейчас — по желанию; можно ввести как `MUST` для новых контрибьюторов.
- **OQ-CV-002:** фиксировать ли версию Python в `pyproject.toml` явно (например, `requires-python = ">=3.11,<3.13"`)?
