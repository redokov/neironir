# Quickstart

Краткая инструкция по развёртыванию neironir на локальной машине.

## Требования

- Python 3.11+
- `git`
- `uv` (https://docs.astral.sh/uv/) — менеджер зависимостей
- (опционально) `opf` из подмодуля `privacy-filter` — для реальной модели

## Установка

```bash
# 1. Клонируем репозиторий с подмодулями
git clone git@github.com:redokov/neironir.git
cd neironir

# 2. Подтягиваем privacy-filter как submodule
git submodule update --init --recursive

# 3. Устанавливаем зависимости backend через uv
uv sync
```

> Шаг с `pip install -e ./privacy-filter` **необязателен**. Backend в режиме
> `mock` (по умолчанию) использует regex-эвристики и работает без модели.
> Подключение реальной модели — отдельный раздел ниже.

## Запуск (mock, без модели)

```bash
uv run uvicorn neironir.main:app --reload --host 127.0.0.1 --port 8000
```

Открыть в браузере: http://127.0.0.1:8000/

## Запуск с реальной моделью (opf)

```bash
# 1. Установить opf и его ML-зависимости
cd privacy-filter
python -m pip install -e .
# при первом запуске opf скачает чекпойнт (~1.5 ГБ) в ~/.opf/privacy_filter
cd ..

# 2. Запустить backend в режиме subprocess
make privacy-run
# (эквивалент: NEIRONIR_PRIVACY_FILTER_MODE=subprocess uv run uvicorn neironir.main:app)
```

> В режиме `subprocess` backend стартует `opf` как дочерний процесс на каждый
> запрос. Чекпойнт монтируется один раз, не на каждый вызов. На CPU один
> документ на десятки страниц обрабатывается десятки секунд — задайте
> `NEIRONIR_PRIVACY_FILTER_TIMEOUT` с запасом.

## Что работает сразу

- На странице — drop-zone.
- Загрузка `.md` или `.docx` → `POST /api/v1/documents` возвращает `202` и `job_id`.
- Поллинг `GET /api/v1/documents/{id}` → отображение «Готово» и кнопки «Скачать».
- Скачивание очищенного файла в исходном формате через `GET /api/v1/documents/{id}/download`.

## Частые ошибки

- **«Файл больше 20 МБ»** — увеличьте `NEIRONIR_MAX_FILE_SIZE` (в байтах).
- **«Nepodдерживаемый формат файла»** — поддерживаются только `.md` и `.docx`.
- **«opf timeout after Ns»** — поднимите `NEIRONIR_PRIVACY_FILTER_TIMEOUT` или
  переключитесь на GPU (`NEIRONIR_PRIVACY_FILTER_DEVICE=cuda`).
- **«opf exited N»** — проверьте, что `opf` доступен в PATH (`opf --help`) и
  чекпойнт либо скачан в `~/.opf/privacy_filter`, либо задан через
  `OPF_CHECKPOINT` / `NEIRONIR_PRIVACY_FILTER_CHECKPOINT_DIR`.

## Проверка (локально)

```bash
make check         # ruff + mypy + pytest
make test-cov      # coverage report
```

CI на GitHub Actions: `.github/workflows/ci.yml` — линт, типы, тесты с
coverage-gate 70%.

## Обновление submodule

```bash
git submodule update --remote privacy-filter
```

Это подтянет свежий коммит из `openai/privacy-filter`. Наш код адаптируется
через адаптер `PrivacyFilterClient` (`backend/neironir/privacy/client.py`).

## Полезные ссылки

- [API](./api.md) — описание эндпоинтов и форматов.
- [Architecture](./architecture.md) — поток данных, ограничения MVP.
- [agents/05-tests-and-docs.md](./agents/05-tests-and-docs.md) — критерии приёмки фазы 5.
