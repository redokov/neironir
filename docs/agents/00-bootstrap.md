# Фаза 0. Bootstrap

## Цель

Подготовить каркас проекта: структура каталогов, инструменты разработки, базовый `pyproject.toml`, версия Python, линт/формат, простейший `hello-world` эндпоинт, чтобы убедиться, что всё запускается.

## Входные условия

- Репозиторий инициализирован, на ветке `main` есть коммит с `README.md` и `.gitignore`.
- Python 3.11+ установлен.
- `uv` установлен (`uv --version`).

## Что делаем

### 1. Файлы

- Создать `pyproject.toml` (используем `uv` для менеджмента, но формат — стандартный PEP 621):
  - `name = "neironir"`
  - `version = "0.0.1"`
  - `requires-python = ">=3.11"`
  - Зависимости runtime:
    - `fastapi>=0.110`
    - `uvicorn[standard]>=0.27`
    - `python-multipart>=0.0.9` (для upload)
    - `python-docx>=1.1` (для `.docx`)
    - `pydantic>=2.6`
    - `pydantic-settings>=2.2`
  - Зависимости dev (отдельная группа `[dependency-groups]` или `[project.optional-dependencies.dev]`):
    - `pytest>=8.0`
    - `pytest-asyncio>=0.23`
    - `httpx>=0.27` (для TestClient FastAPI)
    - `ruff>=0.4`
    - `mypy>=1.10`
  - Скрипт `neironir = "neironir.main:app"` — **не** для uvicorn, не нужен; uvicorn запускается через `uv run uvicorn neironir.main:app`.

- Создать `.python-version` со значением `3.11` (или `3.12`, если согласовано).
- Создать `backend/` (см. ниже).
- Создать `tests/` с `__init__.py` и `test_bootstrap.py`.
- Обновить `.gitignore` (если нужно): `storage/`, `.venv/`, `*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`.

### 2. Структура backend

Создать `backend/neironir/` как Python-пакет (на этапе 0 — пакет лежит в `backend/`, не в корне; это упростит будущее разделение). Файлы:

```
backend/
└── neironir/
    ├── __init__.py
    ├── main.py
    └── config.py
```

- `__init__.py` пустой.
- `config.py`:
  - `pydantic_settings.BaseSettings` с префиксом `NEIRONIR_`.
  - Поля: `host`, `port`, `storage_dir`, `max_file_size`, `privacy_filter_cmd`, `privacy_filter_timeout`, `log_level`.
  - Значения по умолчанию — как в `docs/api.md` → раздел «Конфигурация».
- `main.py`:
  - `create_app()` → `FastAPI(title="neironir", version="0.0.1")`.
  - Эндпоинт `GET /api/v1/health` → `{"status": "ok"}`.
  - Подключение CORS — пока **не нужно**, UI раздаётся с того же origin.

> **Решение по структуре:** пакет `neironir` лежит в `backend/neironir/`, не в корне. Чтобы `uvicorn backend.neironir.main:app` работал, а `uv run uvicorn ...` находил модуль, в `pyproject.toml` настроить `[tool.hatch.build.targets.wheel] packages = ["backend/neironir"]` (если используем hatch backend) **или** поставить sys.path через `tool.uv` / `tool.pytest` (`pythonpath = ["backend"]`). Согласованный вариант: **`pythonpath = ["backend"]`** — проще, не зависит от build-бэкенда.

### 3. `pyproject.toml`: раздел `[tool.pytest.ini_options]`

```toml
[tool.pytest.ini_options]
pythonpath = ["backend"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### 4. `pyproject.toml`: разделы `[tool.ruff]` и `[tool.mypy]`

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
files = ["backend/neironir"]
```

### 5. GitHub Actions (опционально, по решению)

Файл `.github/workflows/ci.yml`:

- Триггер: push, pull_request.
- Матрица: Python 3.11.
- Шаги: checkout, setup-python, install uv, `uv sync`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`.

> Если пользователь не хочет CI — пропустить. Решение по умолчанию: **не создавать CI на этом этапе**. Если нужен — добавим.

### 6. Тесты

`tests/test_bootstrap.py`:

```python
from fastapi.testclient import TestClient
from neironir.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

### 7. README

Дополнить `README.md` разделом «Структура» (фрагмент из `docs/architecture.md`).

## Критерии приёмки

- [ ] `uv sync` отрабатывает без ошибок.
- [ ] `uv run pytest` — 1 зелёный тест.
- [ ] `uv run ruff check .` — без ошибок.
- [ ] `uv run ruff format --check .` — без ошибок.
- [ ] `uv run uvicorn neironir.main:app --port 8000` запускается, `GET /api/v1/health` отдаёт 200.
- [ ] Структура каталогов соответствует `docs/architecture.md` (для тех частей, которые относятся к фазе 0).

## Команды для проверки

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy backend/neironir
uv run uvicorn neironir.main:app --port 8000
curl http://127.0.0.1:8000/api/v1/health
```

## Вне scope

- Подключение `privacy-filter` (фаза 1).
- Доменные модели (фаза 2).
- Любая обработка файлов.
- UI.
