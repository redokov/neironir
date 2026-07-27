# Фаза 2. Доменная модель и контракты API

## Цель

Создать **доменную модель** (Python-классы) и **Pydantic-схемы** для HTTP API, без поднятия самого эндпоинта (это фаза 3). Это даст стабильный контракт, на который опираются и backend, и frontend.

## Входные условия

- Фаза 0 завершена.
- Фаза 1 завершена; список типов сущностей подтверждён.

## Что делаем

### 1. Доменная модель — `backend/neironir/domain/`

Файлы:

- `__init__.py` пустой.
- `entity_type.py`:
  - `class EntityType(str, Enum)` с **8 значениями**, имена — `snake_case` (как в [architecture.md](../architecture.md), колонка «Тип в privacy-filter»):
    ```python
    PRIVATE_PERSON = "private_person"
    PRIVATE_ADDRESS = "private_address"
    PRIVATE_EMAIL = "private_email"
    PRIVATE_PHONE = "private_phone"
    PRIVATE_DATE = "private_date"
    PRIVATE_URL = "private_url"
    ACCOUNT_NUMBER = "account_number"
    SECRET = "secret"
    ```
  - Словарь `TEMPLATE_FORMAT: dict[EntityType, str]`, где шаблон — `"<PRIVATE_PERSON{n}>"`, `"<PRIVATE_ADDRESS{n}>"`, …
- `placeholder.py`:
  - `class PlaceholderCounter`:
    - конструктор без аргументов, инициализирует 8 счётчиков (по одному на `EntityType`) нулями.
    - метод `next(entity_type: EntityType) -> str` — возвращает `<TEMPLATE{n}>` и инкрементирует счётчик.
  - Логика простая, без I/O.
- `job.py`:
  - `class JobStatus(str, Enum)`: `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`.
  - `class Job`:
    - `id: UUID`
    - `status: JobStatus`
    - `source_filename: str`
    - `source_ext: Literal["md", "docx"]`
    - `created_at: datetime`
    - `finished_at: datetime | None`
    - `error: str | None`
    - метод `to_dict()` / `from_dict()` для сериализации в JSON (используется в `storage/local.py` в фазе 3).

### 2. Pydantic-схемы API — `backend/neironir/api/schemas.py`

(В этой фазе — только модели, **роутер не создаём**. Это фаза 3.)

- `JobResponse` — зеркало `Job` в формате JSON для API. Поля:
  - `id: UUID`
  - `status: Literal["pending", "processing", "completed", "failed"]`
  - `source_filename: str`
  - `source_ext: Literal["md", "docx"]`
  - `created_at: datetime`
  - `finished_at: datetime | None`
  - `error: str | None`
  - Конфиг `model_config = ConfigDict(from_attributes=True)` — чтобы можно было собрать из ORM/доменного объекта.
- `HealthResponse`:
  - `status: Literal["ok"]`
- `ErrorResponse`:
  - `code: str`
  - `message: str`

> **Имена статусов в API** — строчные `"pending"` и т.д. (как в `docs/api.md`), в коде — `UPPER_SNAKE`. Конверсия — в маппере API-слоя (фаза 3).

### 3. Тесты

`tests/unit/domain/`:
- `test_entity_type.py`:
  - `EntityType` содержит ровно 8 значений.
  - `TEMPLATE_FORMAT` покрывает все типы.
  - Имена типов соответствуют `architecture.md` (явные ассерты на строки).
- `test_placeholder.py`:
  - Для каждого типа — первый вызов `next` возвращает шаблон с `n=1`, второй — с `n=2`.
  - Счётчики **изолированы по типу** (PRIVATE_PERSON не сбивает PRIVATE_EMAIL).
  - Каждый новый экземпляр `PlaceholderCounter` начинает с 1 (а не продолжает глобальный счёт).
- `test_job.py`:
  - `Job.to_dict()` / `from_dict()` round-trip сохраняет все поля.
  - Сериализация в JSON и обратно — корректна (проверить, что `datetime` сериализуется в ISO 8601).

`tests/unit/api/`:
- `test_schemas.py`:
  - `JobResponse` принимает значения как из `Job` (через `from_attributes=True`).
  - `JobResponse.status` принимает только 4 допустимых строки.

## Критерии приёмки

- [ ] Все 8 значений `EntityType` и `TEMPLATE_FORMAT` совпадают с `architecture.md`.
- [ ] `uv run pytest` — все unit-тесты зелёные.
- [ ] `uv run ruff check .` и `uv run ruff format --check .` — без ошибок.
- [ ] `uv run mypy backend/neironir` — без ошибок.
- [ ] Никаких новых HTTP-эндпоинтов (кроме существующего `/api/v1/health`).

## Вне scope

- FastAPI-роутеры (фаза 3).
- Работа с файлами и хранилищем (фаза 3).
- UI (фаза 4).
- Реальная интеграция с privacy-filter (фаза 3).

## Открытые вопросы

- Если в фазе 1 выяснится, что privacy-filter отдаёт **не** те имена типов — нужно остановить фазу 2 и согласовать изменения в `EntityType` и `architecture.md` с пользователем.
