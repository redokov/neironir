# Quickstart

Краткая инструкция по развёртыванию neironir на локальной машине.

## Требования

- Python 3.11+
- `git`
- `uv` (https://docs.astral.sh/uv/) — менеджер зависимостей
- ~2 ГБ свободного места под модель privacy-filter (зависит от её реального размера)

## Установка

```bash
# 1. Клонируем репозиторий с подмодулями
git clone git@github.com:redokov/neironir.git
cd neironir

# 2. Подтягиваем privacy-filter как submodule
git submodule update --init --recursive

# 3. Устанавливаем зависимости backend через uv
uv sync

# 4. (Опционально) ставим зависимости privacy-filter — следуем инструкции в privacy-filter/README.md
cd privacy-filter
#   действия, которые требуются для запуска их CLI (например, pip install -e .)
cd ..
```

> Сценарий вызова `privacy-filter` (CLI, аргументы, формат ввода/вывода) будет зафиксирован в [agents/03-backend.md](./agents/03-backend.md) после исследования submodule. До этого момента шаг 4 — заглушка.

## Запуск

```bash
# из корня репозитория
uv run uvicorn neironir.main:app --reload --host 127.0.0.1 --port 8000
```

Открыть в браузере: http://127.0.0.1:8000/

## Что должно работать сразу

- На странице — drop-zone.
- Загрузка `.md` или `.docx` → ответ `job_id`.
- Поллинг статуса → отображение «Готово» и кнопки «Скачать».
- Скачивание очищенного файла в исходном формате.

## Что может не работать на старте

- Если `privacy-filter` не установлен локально, любой запуск задачи упадёт с ошибкой. В логах backend будет подсказка, чего не хватает.
- Большие `.docx` (>10 МБ) обрабатываются медленно. См. лимиты в [api.md](./api.md).

## Проверка

```bash
# типы и линт
uv run ruff check .
uv run ruff format --check .

# тесты
uv run pytest
```

## Обновление submodule

```bash
git submodule update --remote privacy-filter
```

Это подтянет свежий коммит из `openai/privacy-filter`. Наш код адаптируется через адаптер `PrivacyFilterClient` (см. [agents/03-backend.md](./agents/03-backend.md)).
