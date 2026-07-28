# Как запустить neironir на этом ноутбуке

## Быстрый старт (режим mock — без модели)

Просто чтобы открыть браузер и попробовать:

```bash
cd /c/MyProjects/neurodoc
.venv/Scripts/uvicorn neironir.main:app --reload --port 8000
```

Открыть в браузере: http://127.0.0.1:8000/

Загрузить `.md` или `.docx` → через секунду «Готово» → скачать.

---

## С реальной моделью (opf) — лучше качество

Модель **уже скачана** (2.7 ГБ, чекпойнт в `~/.opf/privacy_filter/`).

```bash
cd /c/MyProjects/neurodoc

# 1. Убедиться, что opf виден
.venv-opf/Scripts/opf --help

# 2. Запустить приложение с реальной моделью
NEIRONIR_PRIVACY_FILTER_MODE=subprocess  \
NEIRONIR_PRIVACY_FILTER_CMD=".venv-opf/Scripts/opf.exe"  \
NEIRONIR_PRIVACY_FILTER_DEVICE=cpu  \
.venv/Scripts/uvicorn neironir.main:app --reload --port 8000
```

Открыть http://127.0.0.1:8000/ — всё то же самое, но **detect + redact лучше**:
- Mock: email, телефон, URL, дата, номер счёта, пароль
- Реальная модель: всё выше + **имена** (`<PRIVATE_PERSON1>`) и **адреса** (`<PRIVATE_ADDRESS1>`)

Обработка на CPU занимает ~1–2 с на небольшой документ. Если документы большие — можно поднять таймаут:
```
NEIRONIR_PRIVACY_FILTER_TIMEOUT=600
```

---

## Тесты

После любого из запусков — проверить, что всё цело:

```bash
cd /c/MyProjects/neurodoc
.venv/Scripts/python -m pytest -m "not real_model"   # все обычные тесты (mock)
.venv/Scripts/python -m ruff check .                # линт
.venv/Scripts/python -m mypy backend/neironir       # типы
.venv/Scripts/python -m pytest --cov=backend/neironir --cov-report=term-missing  # покрытие
```

### Тесты с реальной моделью (обязательно перед релизом)

Набор тестов, которые прогоняются через настоящий OPF-процесс. По
умолчанию пропускаются (требуют установленной модели и занимают
~3 минуты). Запускаются через `make test-real` или напрямую:

```bash
# 1. Убедиться, что opf на месте
.venv-opf/Scripts/opf.exe --help

# 2. Прогнать — должны пройти все 6 тестов:
cd /c/MyProjects/neurodoc
NEIRONIR_PRIVACY_FILTER_CMD=".venv-opf/Scripts/opf.exe" \
NEIRONIR_RUN_REAL_MODEL_TESTS=1 \
.venv/Scripts/python -m pytest -m real_model -v

# или через Makefile:
make test-real
```

Что проверяется:

- `TestRealModelDetection` — реальная модель находит ФИО, адреса, email
  (mock этого не умеет — критично для уверенности, что прод работает).
- `TestRealModelDocxToMarkdown` — `docx → md` через pandoc + реальный OPF
  не ломает offsets, плейсхолдеры появляются в правильных местах.
- `TestRealModelApplyFeedback` — `apply-feedback` корректно работает на
  выводе реальной модели (offsets не смещены, отклонение реального
  PII восстанавливает оригинальный текст).
- `TestRealModelModeReporting` — endpoint `/api/v1/mode` показывает все
  8 категорий, когда сервер работает в subprocess-режиме.

Этот набор **должен** проходить перед каждой сборкой (`make pre-release`),
иначе есть риск, что изменения в pipeline ломают только реальную модель
(а не mock).

---

## Если что-то пошло не так

| Симптом | Решение |
|---|---|
| `ModuleNotFoundError: No module named '...'` | Не установлены зависимости: `.venv/Scripts/python -m pip install -e .` |
| `opf: command not found` | opf не в PATH. Используй полный путь: `.venv-opf/Scripts/opf.exe` |
| `Default OPF checkpoint ... incomplete` | Чекпойнт повреждён. Удали `~/.opf/privacy_filter/*.safetensors` и перезапусти — скачается заново |
| 413 при загрузке файла | Файл больше 20 МБ. Увеличь `NEIRONIR_PRIVACY_FILTER_TIMEOUT` (ирония — это `NEIRONIR_MAX_FILE_SIZE`) |
| 400 при загрузке `.md` | Проверь расширение — только `.md` и `.docx` (регистр не важен) |
| backend не отвечает на localhost:8000 | Проверь, что uvicorn не упал с ошибкой в консоли |

---

## Полезные команды

```bash
# Загрузить файл через curl
curl -X POST -F "file=@document.md" http://127.0.0.1:8000/api/v1/documents/

# Проверить статус задачи
curl http://127.0.0.1:8000/api/v1/documents/<job_id>

# Скачать очищенный файл
curl -OJ http://127.0.0.1:8000/api/v1/documents/<job_id>/download

# Health check
curl http://127.0.0.1:8000/api/v1/health

# Админский дашборд — открыть в браузере
open http://127.0.0.1:8000/admin
```

---

## Админка

Доступна по адресу `http://127.0.0.1:8000/admin`. Позволяет:

* Посмотреть счётчик обработанных документов (всего / по дням).
* Открыть список документов, по которым оставлена обратная связь, и
  просмотреть найденные сущности + правки пользователя.
* Запустить дообучение модели (`opf train`) на накопленных правках и
  наблюдать за прогрессом (эпоха, loss, ETA).
* Утверждать или отклонять предложенные правила, сгенерированные из
  обратной связи.

API под капотом:

```bash
curl http://127.0.0.1:8000/api/v1/admin/stats
curl http://127.0.0.1:8000/api/v1/admin/documents
curl -X POST "http://127.0.0.1:8000/api/v1/admin/training/start?epochs=3"
curl http://127.0.0.1:8000/api/v1/admin/training/status
curl -X POST http://127.0.0.1:8000/api/v1/admin/training/stop
```

---

## Конвертация .docx → .md

При загрузке `.docx` файла чекбокс «Результат в MD-формате» позволяет
конвертировать документ в markdown с сохранением структуры (заголовки,
списки, выделение). Конвертация выполняется через `pandoc` —
требуется установленный `pandoc` (есть в `chocolatey` / стандартных
репозиториях). Если pandoc недоступен, сервис падает на плоский текст
через `python-docx`.

```bash
# Через API:
curl -X POST \
  -F "file=@contract.docx" \
  -F "output_format=md" \
  http://127.0.0.1:8000/api/v1/documents/
# result.md — markdown с заголовками и плейсхолдерами
curl -OJ http://127.0.0.1:8000/api/v1/documents/<job_id>/download
```

---

## Применение правок к итоговому файлу

В review-секции есть кнопка «Сохранить правки в файл», которая
отправляет пользовательские действия (`add`, `reject`, `confirm`) на
эндпоинт `POST /api/v1/documents/{job_id}/apply-feedback`. Сервис
переписывает `result.{md|docx}` с сохранением сквозной нумерации
плейсхолдеров (`<PRIVATE_EMAIL1>`, `<PRIVATE_EMAIL2>`, …).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/<job_id>/apply-feedback \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"action": "add", "start": 12, "end": 28,
       "entity_type": "private_phone", "text": "+7 495..."},
      {"action": "reject", "start": 0, "end": 16,
       "entity_type": "private_email", "text": "user@example.com",
       "original_span_index": 0}
    ],
    "comment": null
  }'
```

Ответ:

```json
{
  "job_id": "...",
  "applied": 2,
  "added": 1,
  "kept": 0,
  "rejected": 1,
  "output_ext": "md"
}
```
