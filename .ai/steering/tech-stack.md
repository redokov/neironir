# Tech Stack Steering

## Runtime / Platform

- **Python:** 3.11+ (см. `.python-version`).
- **Пакетный менеджер:** [uv](https://github.com/astral-sh/uv) — единственный канонический инструмент (`uv sync`, `uv run`, `uv lock`).
- **ОС-цели:** локальная разработка на Windows/macOS/Linux; production-развёртывание — на машине пользователя (без Docker).
- **Виртуальные окружения:** два venv в репозитории — `.venv/` (основной backend) и `.venv-opf/` (для `privacy-filter` submodule); см. `Makefile`.

## Backend

- **Веб-фреймворк:** FastAPI + Uvicorn.
- **Валидация/сериализация:** Pydantic v2, pydantic-settings для конфигурации.
- **Фоновые задачи:** `BackgroundTasks` FastAPI (без Celery/RQ — см. ограничения MVP).
- **Работа с .docx:** `python-docx`.
- **Работа с .md:** стандартная библиотека Python + regex-обработка.
- **Markdown→docx / docx→markdown (опционально):** Pandoc CLI (с fallback на python-docx).

## Privacy Filter (ML)

- **Репозиторий:** [openai/privacy-filter](https://github.com/openai/privacy-filter.git) — git submodule в `privacy-filter/`.
- **Режимы вызова:**
  - `subprocess` — CLI-вызов `opf` через `subprocess` (production).
  - `mock` — regex-эвристика в `backend/neironir/privacy/client.py` (разработка, CI).
- **In-process Python API** — **вне scope MVP** (см. `docs/architecture.md#ограничения-mvp`).

## Frontend

- **Стек:** vanilla HTML/CSS/JS (без сборщиков, без фреймворков).
- **Точка входа:** `frontend/index.html` + `frontend/app.js` + `frontend/styles.css`.
- **Поведение:** SPA-обёртка с drop-zone, polling статуса задачи, download очищенного файла, секция review/feedback.

## Storage

- **Тип:** локальная файловая система.
- **Структура:**
  - `storage/jobs/{job_id}/source.{ext}` — загруженный файл.
  - `storage/jobs/{job_id}/result.{ext}` — очищенный файл.
  - `storage/jobs/{job_id}/job.json` — состояние Job.
- **Production-каталог:** `storage_prod/` (см. `.gitignore`, `RUN.md`).
- **БД:** не используется.

## Testing / Verification

- **Unit-тесты:** `tests/unit/` — pytest.
- **Integration-тесты:** `tests/integration/` — pytest, в т.ч. `test_pipeline_real_model.py` с маркером `real_model`.
- **E2E-тесты:** `tests/e2e/` — сценарии пользователя.
- **Каналы проверки (Makefile):**
  - `make check` — ruff + mypy + pytest (mock).
  - `make lint` — ruff.
  - `make type` — mypy.
  - `make test` — pytest (mock).
  - `make test-cov` — coverage report, gate ≥ 70%.
  - `make test-real` — прогон с реальной OPF-моделью (`NEIRONIR_RUN_REAL_MODEL_TESTS=1`).
  - `make pre-release` — `lint + type + test + test-real` (полный pre-release чек).
- **Coverage gate:** 70% (см. `.github/workflows/ci.yml`).
- **Тесты `real_model`** пропускаются по умолчанию; запускаются перед релизом (~3 минуты).

## CI

- **Платформа:** GitHub Actions (`.github/workflows/ci.yml`).
- **Задачи:** lint + type + test (mock) + coverage gate.

## Constraints

- **Один процесс:** backend = один процесс Uvicorn, фоновые задачи в нём же.
- **Без внешних сервисов:** LLM/ML только локальная модель; никаких облачных API для самой очистки.
- **Один пользователь:** без auth-стека (OAuth, JWT, RBAC).
- **Совместимость форматов:** только `.md` и `.docx`. PDF/RTF/изображения — **вне scope**.
- **Pinned versions:** все зависимости зафиксированы в `uv.lock` и `pyproject.toml`; обновления — осознанно через PR.
- **Один карта замен не хранится:** намеренное ограничение ради приватности; нельзя «ослабить» без изменения требований.

## Architectural Decisions

- **Subprocess вместо in-process API для `opf`:** выбрано в `docs/agents/03-backend.md` (фаза 3) — упрощает развёртывание, изолирует падения модели.
- **Mock-режим как first-class:** введён в `01-submodule-and-research.md` — даёт CI-совместимость без тяжёлой модели.
- **Локальная FS вместо БД:** выбрано в `02-domain-and-contracts.md` — для MVP достаточно; легко мигрировать.
- **Pydantic v2 + pydantic-settings:** выбрано как канонический стек валидации; Pydantic v1 не поддерживается.
- **Pandoc с fallback:** для `.docx→.md` используется Pandoc, при его отсутствии — плоский текст через `python-docx`. Решение зафиксировано в `docs/architecture.md`.

## Open Questions

- **OQ-TS-001:** нужен ли второй сторонний пакетный менеджер (`poetry`, `pip-tools`) для какой-то группы пользователей? Текущий канон — `uv`; при появлении новых контрибьюторов — пересмотреть.
- **OQ-TS-002:** целесообразно ли выделение `privacy-filter` в отдельный Python-пакет, импортируемый напрямую (а не через CLI)? Сейчас — `subprocess`; см. `docs/agents/privacy-filter-research.md`.
- **OQ-TS-003:** добавлять ли опциональный JSON-манифест результата (без reverse map) для аудита замен? Сейчас — не хранится ничего кроме `result.{ext}`.
