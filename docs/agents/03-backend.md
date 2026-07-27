# Фаза 3. Backend

## Цель

Реализовать FastAPI-приложение с тремя эндпоинтами (upload, status, download), конвертерами `.md`/`.docx`, адаптером к `privacy-filter`, файловым хранилищем и пайплайном обработки. После этой фазы backend способен принять файл, прогнать через модель и отдать очищенный файл того же формата.

## Входные условия

- Фазы 0, 1, 2 завершены.
- `EntityType`, `PlaceholderCounter`, `Job`, `JobStatus`, Pydantic-схемы существуют.
- `docs/agents/privacy-filter-research.md` существует и содержит подтверждённый способ вызова.

## Структура

Новые/изменённые файлы:

```
backend/neironir/
├── main.py                         # обновить: подключить роутер, раздача static
├── api/
│   ├── __init__.py
│   ├── jobs.py                     # новый: /api/v1/documents
│   ├── ui.py                       # новый: GET / → frontend/index.html
│   └── schemas.py                  # перенесли из фазы 2, если ещё не здесь
├── domain/                         # из фазы 2
├── converters/
│   ├── __init__.py
│   ├── base.py                     # DocumentConverter (Protocol)
│   ├── markdown.py                 # MarkdownConverter
│   └── docx.py                     # DocxConverter
├── privacy/
│   ├── __init__.py
│   └── client.py                   # PrivacyFilterClient
├── storage/
│   ├── __init__.py
│   └── local.py                    # LocalStorage
└── workers/
    ├── __init__.py
    └── pipeline.py                 # run_job(job_id, settings, storage, ...)
```

```
frontend/
├── index.html
├── app.js
└── styles.css
```

> `frontend/index.html` пока может быть **заглушкой** (просто `<h1>neironir</h1>`) — наполнение в фазе 4. Главное — роутинг `GET /` уже работает.

## 1. Конвертеры

### `backend/neironir/converters/base.py`

```python
from typing import Protocol
from pathlib import Path
from neironir.domain.entity_type import EntityType


class DocumentConverter(Protocol):
    ext: str  # "md" или "docx"

    def extract_text(self, source: Path) -> str: ...
    def build(self, source: Path, target: Path, replacements: list[Replacement]) -> None: ...
```

Где `Replacement` — `dataclass`:
```python
@dataclass(frozen=True)
class Replacement:
    start: int        # inclusive, по тексту
    end: int          # exclusive, по тексту
    entity_type: EntityType
    placeholder: str  # уже сформированный шаблон, например "<PRIVATE_PERSON1>"
```

> Конвертер работает с **позициями в извлечённом тексте**. Не с разметкой markdown/HTML. Это упрощает интеграцию с privacy-filter, который работает с сырым текстом.

### `backend/neironir/converters/markdown.py`

- `MarkdownConverter`:
  - `extract_text(source: Path) -> str` — читает файл как UTF-8, возвращает строку.
  - `build(source, target, replacements)`:
    - перечитывает исходник,
    - **сортирует replacements по `start` убыванием** и применяет их к тексту, чтобы индексы оставались валидными,
    - пишет результат в `target` в UTF-8.

### `backend/neironir/converters/docx.py`

- `DocxConverter`:
  - Использует `python-docx` (`Document`).
  - `extract_text(source) -> str`:
    - проходит по всем параграфам `document.paragraphs` **в порядке документа**,
    - склеивает текст параграфов через `\n`,
    - возвращает строку.
  - `build(source, target, replacements)`:
    - **Важно:** `.docx` — не plain text, у него есть параграфы и runs. Алгоритм:
      1. Распаковываем replacements по индексам в плоской строке → в (paragraph_index, char_offset_in_paragraph, length, placeholder).
      2. Проходим по параграфам и заменяем соответствующие участки текста.
    - Сохраняем в `target`.
  - **Упрощение MVP:** не сохраняем форматирование (жирный, курсив, ссылки), только plain text внутри параграфов. Сноски, таблицы, картинки, списки — игнорируем (только параграфы). Это явно зафиксировать в коде комментарием и в `docs/architecture.md` после реализации (добавить в раздел «Ограничения MVP»).
  - **Алгоритм разметки по параграфам:**
    - Получаем `paragraph_texts: list[str]` — тексты всех параграфов.
    - Строим `cumulative_offsets: list[int]` — длина cumulative (например, `[0, len(p1)+1, len(p1)+len(p2)+2, ...]`).
    - Для каждого `Replacement(start, end)`:
      - Бинарным поиском или линейным проходом определяем, в каких параграфах он лежит.
      - Если `Replacement` лежит **внутри одного параграфа** — заменяем `text[local_start:local_end] = placeholder`.
      - Если пересекает **границу параграфов** — замену нужно либо разделить, либо (упрощение MVP) вынести целиком в один из параграфов и снести всё, что между, в этот же параграф. Реализовать **разбиение**: для каждой замены — список (paragraph_index, start_in_paragraph, end_in_paragraph, text_to_insert). Применять слева направо по параграфам; placeholder вставляется в той точке, где начинается замена; всё, что после — продолжается в исходном виде.
    - **Проверка:** на интеграционном тесте (фаза 5) с документом, где сущность пересекает параграф, поведение должно быть **детерминированным и не падать**. В спорных случаях — выбрасываем `ValueError("Replacement crosses paragraph boundary")` с понятным сообщением. Это лучше, чем молча терять сущности.

> Если агент посчитает, что задача аккуратной обработки `.docx` слишком велика для фазы 3 — вынести её в отдельную мини-фазу 3.1 и попросить пользователя подтвердить объём.

## 2. Хранилище — `backend/neironir/storage/local.py`

Класс `LocalStorage`:
- `__init__(self, root: Path)`.
- `create_job_dir(job_id: UUID) -> Path` — создаёт `root/jobs/{job_id}/`, возвращает путь.
- `save_source(job_id, filename, content: bytes) -> tuple[Path, str]`:
  - определяет расширение (`.md` или `.docx`),
  - пишет в `root/jobs/{job_id}/source.{ext}`,
  - возвращает `(path, ext)`.
- `save_result(job_id: UUID, ext: str, content: bytes) -> Path`.
- `load_job(job_id) -> Job` — читает `job.json`, десериализует.
- `save_job(job: Job) -> None` — атомарно (через временный файл + rename).
- `result_path(job_id) -> Path` — `root/jobs/{job_id}/result.{ext}`.
- `job_dir(job_id) -> Path`.

Все пути — через `pathlib.Path`, никаких строковых конкатенаций. Без блокировок: пишем только из `BackgroundTasks`/из API-обработчика, других потоков нет.

## 3. Адаптер privacy-filter — `backend/neironir/privacy/client.py`

Это **заглушка + контракт**. Реальный вызов — **после** завершения фазы 1; в этой фазе используем **mock-режим** для разработки и тестов.

```python
class PrivacyFilterClient(Protocol):
    async def annotate(self, text: str) -> list[EntitySpan]: ...


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    entity_type: EntityType
```

Две реализации:

### `MockPrivacyFilterClient`

- Использует **регулярные выражения** для базового распознавания:
  - `private_email` — `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`,
  - `private_phone` — `\+?\d[\d\s\-()]{7,}\d`,
  - `private_url` — `https?://\S+`,
  - `private_date` — `\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b`,
  - `account_number` — `\b\d{16,20}\b`,
  - `secret` — `(?i)(?:password|passwd|pwd)\s*[:=]\s*\S+`,
  - `private_person` — **намеренно пусто** (mock не пытается), будем ждать реальную модель,
  - `private_address` — **намеренно пусто**.
- Дедупликация: пересекающиеся спаны оставляем с приоритетом более специфичного правила (например, email важнее phone, если пересеклись — отбрасываем phone).
- Используется во время разработки UI и для интеграционных тестов, **когда реальная модель недоступна**.

### `SubprocessPrivacyFilterClient`

- Шаблон (наполняется в фазе 1):
  - команда из `settings.privacy_filter_cmd` (`NEIRONIR_PRIVACY_FILTER_CMD`),
  - текст через stdin или временный файл — что выберет исследование,
  - stdout — JSON со списком `EntitySpan`,
  - `asyncio.create_subprocess_exec` + `communicate()` с `timeout=settings.privacy_filter_timeout`,
  - парсинг результата, маппинг строковых типов в `EntityType` (с проверкой, что каждая строка — допустимый тип).
- Если вывод пуст — отдаём `[]`.
- Ошибки выполнения (ненулевой код, таймаут, невалидный JSON, неизвестный тип) → `PrivacyFilterError`.

> **В этой фазе** в `main.py` подключаем `MockPrivacyFilterClient` по умолчанию. `SubprocessPrivacyFilterClient` создаём как **заготовку** с `raise NotImplementedError` в `annotate()`, и в `main.py` переключение через `settings.privacy_filter_mode` (`mock` | `subprocess`).

## 4. Пайплайн — `backend/neironir/workers/pipeline.py`

```python
async def run_job(
    job_id: UUID,
    *,
    settings: Settings,
    storage: LocalStorage,
    privacy: PrivacyFilterClient,
) -> None:
```

Шаги:
1. Загрузить `Job`, поставить `status=PROCESSING`, сохранить.
2. `source_path, ext = storage.source_path(job_id)`.
3. Получить конвертер по `ext` (реестр `CONVERTERS: dict[str, DocumentConverter]`).
4. `text = converter.extract_text(source_path)`.
5. `spans = await privacy.annotate(text)`.
6. Отсортировать `spans` по `start`.
7. Создать `PlaceholderCounter`. Пройти по `spans` в порядке возрастания `start`:
   - `placeholder = counter.next(span.entity_type)`,
   - `replacements.append(Replacement(span.start, span.end, span.entity_type, placeholder))`.
8. `target_path = storage.result_path(job_id)`, `converter.build(source_path, target_path, replacements)`.
9. Поставить `status=COMPLETED`, `finished_at=now`, сохранить.
10. На любом исключении — `status=FAILED`, `error=str(e)`, `finished_at=now`, сохранить, залогировать.

## 5. API — `backend/neironir/api/jobs.py`

Роутер `APIRouter(prefix="/api/v1/documents", tags=["documents"])`.

| Метод | Путь | Поведение |
|---|---|---|
| POST | `/` | multipart `file` → проверка расширения и размера → создать `Job(status=PENDING)` → сохранить source → запустить `BackgroundTasks.add_task(run_job, ...)` → вернуть `JobResponse`, код `202` |
| GET | `/{job_id}` | прочитать Job → вернуть `JobResponse` или `404` |
| GET | `/{job_id}/download` | если `status != COMPLETED` → `409`; иначе `FileResponse(result_path, filename=f"{orig}.cleaned.{ext}")` |

`/api/v1/health` остаётся.

> **Монгирование дипов:** см. `docs/api.md` — `Content-Disposition: <original>.cleaned.<ext>`. Имя берём из `Job.source_filename`, отрезаем исходное расширение и приклеиваем `.cleaned.<ext>`. Если в имени файла несколько точек — режем по последней.

## 6. UI-роут — `backend/neisonir/api/ui.py`

```python
@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(settings.frontend_dir / "index.html")
```

`settings.frontend_dir` — `Path("frontend")` (относительно `pyproject.toml`).

Подключить в `main.py`:
- `app.mount("/static", StaticFiles(directory="frontend"), name="static")` (если будут css/js по путям `/static/...`).
- `app.include_router(ui.router)`.
- `app.include_router(jobs.router)`.

CORS — **не добавляем**, фронт с того же origin.

## 7. Тесты

`tests/unit/`:
- `converters/test_markdown.py`:
  - extract → round-trip с заменой → текст совпадает с ожидаемым (на маленьком примере).
  - замены, идущие в обратном порядке в списке, дают корректный результат (проверка сортировки).
- `converters/test_docx.py`:
  - на маленьком `.docx` фикстуре: extract → round-trip с заменой → текст параграфов совпадает с ожидаемым.
  - кейс «замена пересекает границу параграфов» → ожидаемое `ValueError` (или документированное поведение).
- `privacy/test_mock_client.py`:
  - на тексте с email/phone/url — спаны находятся, типы корректные, пересечения устранены.
  - порядок: сначала более специфичный тип.
- `storage/test_local.py`:
  - save → load_job round-trip, пути корректны.

`tests/integration/`:
- `test_pipeline_markdown.py`:
  - создать `LocalStorage` во временной папке,
  - создать `Job` руками,
  - сохранить текстовый источник,
  - вызвать `run_job` с `MockPrivacyFilterClient`,
  - дождаться завершения,
  - assert: `status=COMPLETED`, `result_path` существует, в файле нет email, есть `<PRIVATE_EMAIL1>`.
- `test_pipeline_docx.py` — аналогично на docx-фикстуре.
- `test_api.py`:
  - `TestClient(create_app())`, загрузка через `files=...`, проверка `202`, `GET /{id}` → `pending` → `completed`, `GET /{id}/download` → корректный файл.

Фикстуры (`.docx`):
- Создаются **программно** через `python-docx` в `tests/integration/conftest.py`, без бинарных блобов в репозитории. Это сохраняет репозиторий «чистым».

## Критерии приёмки

- [ ] `uv run pytest` — все тесты зелёные (unit + integration).
- [ ] `uv run ruff check .` и `uv run ruff format --check .` — без ошибок.
- [ ] `uv run mypy backend/neironir` — без ошибок.
- [ ] `uv run uvicorn neironir.main:app --reload` запускается.
- [ ] Через `curl` можно загрузить файл, получить `job_id`, дополлировать до `completed`, скачать очищенный файл.
- [ ] End-to-end сценарий с `.md` отрабатывает: email в тексте заменяется на `<PRIVATE_EMAIL1>`.
- [ ] End-to-end с `.docx` отрабатывает на фикстуре: текст изменён, формат docx сохранён.
- [ ] Неподдерживаемое расширение → `400`.
- [ ] Превышение размера → `413`.
- [ ] Попытка скачать незавершённую задачу → `409`.

## Вне scope

- UI-код (фаза 4).
- Реальный `SubprocessPrivacyFilterClient` (после фазы 1, но в этой фазе — заглушка; фактическое подключение — фаза 3.1 / 5, если потребуется).
- Тесты производительности.
- Логирование в файл / структурное логирование (допустимо `logging.basicConfig` на этом этапе; structlog — фаза 5+).

## Зависимости

`pyproject.toml`:
- Добавить в runtime: `python-docx>=1.1` (если не добавлен в фазе 0).
- Опциональная группа `ml` — **не** добавляем до фазы фактического подключения (тогда, когда в фазе 1 будет принято решение).

## Замечание по объёму

Эта фаза — самая большая. Если агент понимает, что `.docx`-конвертер с обработкой форматирования перегружен — выделить `.docx` в фазу 3.1 (после согласования) и оставить в фазе 3 только `.md` (end-to-end). Это ухудшит UX (нет docx), но не сломает архитектуру.
