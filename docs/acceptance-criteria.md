# Критерии приёмки neironir

Полный чек-лист для проверки, что приложение работает корректно.
Разделы можно проходить по порядку или выборочно.

## 1. Системные требования

- [ ] **Python 3.11+** установлен: `python --version` → `3.11.x` или `3.12.x`
- [ ] **uv** установлен: `uv --version`
- [ ] **git submodule** подтянут: `git submodule update --init --recursive` (privacy-filter/)
- [ ] **Зависимости** установлены: `uv sync` завершается без ошибок
- [ ] **Make** доступен (на Linux/macOS/WSL) — не обязателен; все цели эквивалентны `uv run …`

## 2. Статический анализ

- [ ] **ruff check** — 0 ошибок: `uv run ruff check .`
- [ ] **ruff format** — все файлы отформатированы: `uv run ruff format --check .`
- [ ] **mypy strict** — 0 ошибок: `uv run mypy backend/neironir`

## 3. Тесты

- [ ] **Все тесты зелёные:** `uv run pytest` → `106 passed`
- [ ] **Coverage ≥ 70 %:** `uv run pytest --cov=backend/neironir --cov-report=term-missing` → `TOTAL … 88 %`

## 4. Запуск приложения

- [ ] **uvicorn стартует:** `uv run uvicorn neironir.main:app --port 8000`
      → stdout: `Uvicorn running on http://127.0.0.1:8000`
- [ ] **Health endpoint:** `curl http://127.0.0.1:8000/api/v1/health`
      → `{"status":"ok"}`

## 5. Пользовательский интерфейс (SPA)

- [ ] **`GET /`** отдаёт `index.html`: `curl http://127.0.0.1:8000/`
      → содержит `<h1>neironir</h1>` и `id="dropzone"`
- [ ] **/static/app.js** доступен: статус 200, не пустой
- [ ] **/static/styles.css** доступен: статус 200, не пустой
- [ ] В браузере видна drop-zone с текстом «Перетащите файл сюда или выберите»

## 6. Обработка документов — режим `mock` (по умолчанию)

### Markdown (.md)

- [ ] **Загрузка:** `curl -X POST -F "file=@test.md" http://127.0.0.1:8000/api/v1/documents/`
      → статус `202`, в теле `id`, `status: "pending"`
- [ ] **Поллинг:** `curl http://127.0.0.1:8000/api/v1/documents/<id>`
      → через 1–2 итерации `status: "completed"`
- [ ] **Email заменён** на `<PRIVATE_EMAIL1>` (если `test.md` содержит `user@example.com`)
- [ ] **Телефон заменён** на `<PRIVATE_PHONE1>` (если есть `+7 495 123-45-67` и т.п.)
- [ ] **Дата заменена** на `<PRIVATE_DATE1>` (если есть `01.02.1990`)
- [ ] **URL заменён** на `<PRIVATE_URL1>` (если есть `https://…`)
- [ ] **Номер счёта заменён** на `<ACCOUNT_NUMBER1>` (если есть `\b\d{16,20}\b`)
- [ ] **Секрет заменён** на `<SECRET1>` (если есть `password=…`)
- [ ] **Имя и адрес mock НЕ заменяет** (нет pattern'ов)
- [ ] **Скачивание:** `curl -OJ http://127.0.0.1:8000/api/v1/documents/<id>/download`
      → статус `200`, `Content-Disposition: <stem>.cleaned.md`, в теле — очищенный MD
- [ ] **Формат MD сохранён:** файл читается как текст, структура (заголовки, списки) цела
- [ ] **Round-trip без PII:** документ без sensitive данных проходит без placeholder'ов
      → `<PRIVATE` не встречается в результате

### Microsoft Word (.docx)

- [ ] **Загрузка:** `curl -X POST -F "file=@test.docx" …` → `202`
- [ ] **Скачивание:** файл имеет `Content-Disposition: <stem>.cleaned.docx`
- [ ] **Параграфы сохранены:** каждый параграф из исходника присутствует в результате
- [ ] **Email заменён** на `<PRIVATE_EMAIL1>` внутри параграфа
- [ ] **Формат .docx** (OOXML ZIP) сохранён: `file result.docx` → `Microsoft Word`
      или `unzip -t result.docx` без ошибок
- [ ] **Round-trip без PII:** документ без sensitive данных — placeholder'ы не вставляются

## 7. Обработка документов — режим `subprocess` (реальная модель)

> Требуется установленный `opf` в PATH (см. `docs/quickstart.md`).

- [ ] **Переключение:** `NEIRONIR_PRIVACY_FILTER_MODE=subprocess uv run uvicorn …`
- [ ] **Чекпойнт доступен:** `opf --help` работает, `~/.opf/privacy_filter` содержит `.safetensors`
- [ ] **Email заменён** на `<PRIVATE_EMAIL1>` — модель детектирует email
- [ ] **Имя заменено** на `<PRIVATE_PERSON1>` — модель детектирует person (этого mock не делает)
- [ ] **Адрес заменён** на `<PRIVATE_ADDRESS1>` — модель детектирует address
- [ ] **Таймаут:** обработка длинного документа не обрывается раньше 600 с
      (по умолчанию `NEIRONIR_PRIVACY_FILTER_TIMEOUT`)
- [ ] **Ошибка модели:** если `opf` вернул ненулевой код → `status: "failed"`, `error` содержит детали
- [ ] **Неизвестная метка:** если модель выдала label вне нашего `EntityType` → игнорируется,
      в логе `privacy-filter emitted unknown label: …`

## 8. Обработка ошибок

- [ ] **Неподдерживаемое расширение** (.exe, .txt, .pdf, без расширения) → `400`,
      `code: "unsupported_format"`
- [ ] **Превышение размера** → `413`, `code: "file_too_large"`
      (проверяется при маленьком `NEIRONIR_MAX_FILE_SIZE` или через mock-тесты)
- [ ] **Загрузка без файла** → `422`
- [ ] **Запрос несуществующего job_id** → `404`, `code: "job_not_found"`
- [ ] **Скачивание незавершённой задачи** (pending / processing / failed) → `409`,
      `code: "job_not_ready"`
- [ ] **Malformed UUID** в пути → `422`

## 9. Названия и заголовки

- [ ] **Скачивание:** `Content-Disposition` = `<stem>.cleaned.<ext>`
      (например, `Договор.docx` → `Договор.cleaned.docx`)
- [ ] **Регистронезависимость расширения:** `NOTE.MD` → `source_ext: "md"`, обрабатывается
- [ ] **Плейсхолдеры** соответствуют шаблону `<TYPE{n}>`:
      `<PRIVATE_PERSON1>`, `<PRIVATE_ADDRESS1>`, … `<SECRET1>`
- [ ] **Нумерация** сквозная по каждому типу в пределах одного документа:
      первый email → `<PRIVATE_EMAIL1>`, второй → `<PRIVATE_EMAIL2>`
- [ ] **Нумерация НЕ сквозная по сервису:**
      два последовательных документа оба начинают счёт с `1`

## 10. Concurrent jobs

- [ ] **Несколько одновременных загрузок** — все получают `202`
- [ ] **Все завершаются** `completed`
- [ ] **Счётчики независимы:** каждый документ имеет `PRIVATE_EMAIL1`, `PRIVATE_EMAIL2`, …
      (не `PRIVATE_EMAIL1`, `PRIVATE_EMAIL4`, …)

## 11. CI (GitHub Actions)

- [ ] **Push/PR в main:** workflow запускается
- [ ] **ruff check** — passed
- [ ] **ruff format --check** — passed
- [ ] **mypy** — passed
- [ ] **pytest** — passed (все 106+ тестов)
- [ ] **Coverage** ≥ 70 %
- [ ] **Python 3.11 и 3.12** — оба проходят
- [ ] **Артефакт .coverage** загружается для 3.12

## 12. Состояние хранилища

- [ ] После загрузки файла `storage/jobs/<uuid>/` содержит:
      `source.md` / `source.docx`, `job.json`, (после обработки) `result.md` / `result.docx`
- [ ] `job.json` содержит корректные поля: `id`, `status`, `source_filename`, `source_ext`,
      `created_at`, `finished_at` (или null), `error` (или null)
- [ ] Хранилище не бесконечно растёт (нет автоочистки — это ограничение MVP)

## 13. Ограничения MVP (должны быть явно зафиксированы в документации)

- [ ] В `docs/architecture.md` есть раздел «Ограничения MVP»
- [ ] В `README.md` есть перечень ограничений
- [ ] `.docx` сохраняет только параграфы; форматирование, таблицы, списки, изображения теряются
- [ ] `Replacement`, пересекающий границу параграфов в .docx, выбрасывает `ValueError`
- [ ] Нет аутентификации
- [ ] Нет фоновой очереди (Celery/RQ) — обработка в `BackgroundTasks` внутри того же процесса
- [ ] Нет автопроцесса удаления завершённых задач
- [ ] Нет обратной замены (reverse map) — преобразование однонаправленное
