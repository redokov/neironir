# Фаза 5. Тесты, документация, финальная полировка

## Цель

- Довести тестовое покрытие до состояния «не стыдно»: unit + integration + (опционально) e2e через `TestClient`.
- Дописать документацию, обновить `README.md` под текущее состояние.
- Зафиксировать ограничения MVP явно (в `docs/architecture.md` и `README.md`).
- Подготовить `Makefile` (или `taskfile`) с типовыми командами.

## Входные условия

- Фазы 0–4 завершены, e2e через браузер работает вручную.

## Что делаем

### 1. Тесты

#### Unit (дополнить)

- `tests/unit/converters/test_docx_edge_cases.py`:
  - кейсы: пустой документ, документ с одним параграфом, документ с несколькими параграфами, документ с пересечением замены через границу.
- `tests/unit/privacy/test_mock_dedup.py`:
  - кейсы дедупликации пересекающихся спанов (email vs phone).
- `tests/unit/api/test_jobs_validation.py`:
  - загрузка без файла → `422`,
  - загрузка с неверным типом → `400`,
  - загрузка с превышением размера → `413` (через настройку `max_file_size=1` в фикстуре),
  - `GET /{id}/download` для несуществующего id → `404`,
  - `GET /{id}/download` для `pending` → `409`.

#### Integration (дополнить)

- `tests/integration/test_e2e_md.py`:
  - `TestClient`, реальный файл `.md` (создаётся во временной папке), проверка полного сценария.
- `tests/integration/test_e2e_docx.py`:
  - то же на `.docx`.
- `tests/integration/test_concurrent_jobs.py` (опционально):
  - запустить 3 задачи параллельно через `BackgroundTasks` + `TestClient`, проверить, что все завершаются `completed`.

> Чтобы тесты были детерминированы, **во всех integration-тестах** явно подменять `PrivacyFilterClient` на `MockPrivacyFilterClient` через переопределение зависимостей FastAPI (`app.dependency_overrides`) — это уже заложено в фазе 3.

#### Coverage

- `uv run pytest --cov=backend/neironir --cov-report=term-missing` — зафиксировать процент. Целевой: **≥ 70%** для MVP.
- Если меньше — добавить тесты, не снижая порог.

#### Конфиг pytest

`pyproject.toml`:
```toml
[tool.coverage.run]
source = ["backend/neironir"]

[tool.coverage.report]
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if __name__ == .__main__.:",
]
```

### 2. Документация

- `README.md`:
  - Разделы: «Что это», «Стек», «Структура», «Быстрый старт» (кратко, со ссылкой на `docs/quickstart.md`), «Поддерживаемые форматы», «Ограничения», «Лицензия» (на усмотрение пользователя).
- `docs/architecture.md`:
  - Дополнить раздел «Ограничения MVP» явным пунктом: «`.docx`-конвертер сохраняет только параграфы, форматирование/таблицы/списки игнорируются». (Или, если в фазе 3 было решено иначе — отразить актуальное поведение.)
- `docs/quickstart.md`:
  - Сверить команды с тем, что реально работает после фаз 0–4.
  - Добавить раздел «Частые ошибки»: «Privacy filter не установлен → что делать», «Файл слишком большой → уменьшить или поднять `NEIRONIR_MAX_FILE_SIZE`».
- `docs/agents/privacy-filter-research.md` (если существует) — **оставить как есть**, но добавить дату последнего пересмотра.

### 3. `Makefile`

```makefile
.PHONY: install dev test lint format type check run clean

install:
	uv sync

dev:
	uv run uvicorn neironir.main:app --reload --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

type:
	uv run mypy backend/neironir

check: lint type test

run:
	uv run uvicorn neironir.main:app --host $(or $(HOST),127.0.0.1) --port $(or $(PORT),8000)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov storage/jobs/*
```

> На Windows `make` не всегда есть. Альтернативно — `taskfile.yml` (если `go-task` установлен) или `scripts.py` через `uv run python scripts.py dev`. Решение по инструменту — на усмотрение пользователя; агент предлагает `Makefile` и при необходимости добавляет `scripts.py`.

### 4. `.env.example`

```env
NEIRONIR_HOST=127.0.0.1
NEIRONIR_PORT=8000
NEIRONIR_STORAGE_DIR=./storage
NEIRONIR_MAX_FILE_SIZE=20971520
NEIRONIR_PRIVACY_FILTER_CMD=python -m privacy_filter
NEIRONIR_PRIVACY_FILTER_TIMEOUT=600
NEIRONIR_PRIVACY_FILTER_MODE=mock
NEIRONIR_LOG_LEVEL=INFO
```

Реальный `.env` — в `.gitignore` (он уже там).

### 5. Финальный отчёт

Агент возвращает пользователю:

- Список всех коммитов фазы.
- Команда `make check` — статус.
- Команда `uv run uvicorn ...` — статус (запускается ли).
- Скриншот/описание UI (текстом).
- Известные ограничения.
- Что **не** сделано из исходного плана (если есть).
- Предложение по следующим шагам (фаза 3.1 для `.docx`?, подключение реальной модели?).

## Критерии приёмки

- [ ] `make check` (или эквивалент через `uv`) — зелёный.
- [ ] Coverage ≥ 70%.
- [ ] Все 5 файлов `docs/*.md` обновлены и согласованы между собой.
- [ ] `README.md` — финальная версия.
- [ ] `Makefile` (или альтернатива) работает.
- [ ] `.env.example` существует.
- [ ] В `docs/architecture.md` явно зафиксированы ограничения MVP.

## Вне scope

- CI в GitHub Actions (если не было создано ранее — оставить на потом).
- Реальный `SubprocessPrivacyFilterClient` (фаза 5+).
- Структурное логирование, метрики.
- e2e через Playwright.
- Деплой.
