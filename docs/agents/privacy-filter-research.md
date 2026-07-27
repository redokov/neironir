# privacy-filter — результаты исследования

Исследование внешнего репозитория [openai/privacy-filter](https://github.com/openai/privacy-filter),
подключённого как git submodule в `privacy-filter/`. Цель — зафиксировать контракт
вызова, на который будет опираться адаптер в фазе 3 (`backend/neironir/privacy/client.py`).

## Источник истины

- **Репо:** `https://github.com/openai/privacy-filter` (клон через SSH: `git@github.com:openai/privacy-filter.git`).
- **Submodule зафиксирован на коммите:** `f7f00ca7fb869683eb732c010299d901457f19c3` (HEAD ветки `main` на момент исследования, `2026-04-22`).
- **Файл `.gitmodules`:**

  ```ini
  [submodule "privacy-filter"]
      path = privacy-filter
      url = git@github.com:openai/privacy-filter.git
  ```

- **Команда проверки:**

  ```bash
  git submodule status
  #  f7f00ca7fb869683eb732c010299d901457f19c3 privacy-filter (heads/main)
  ```

## Способ запуска

У репозитория есть **и CLI, и Python API**. На уровне модуля (пакет называется `opf`):

- **CLI:** бинарь `opf` (или `python -m opf`). Имеет три подкоманды — `redact` (по умолчанию), `eval`, `train`. Нам интересна только `redact`.
- **Python API:** `from opf import OPF, RedactionResult, DecodeOptions`. Класс `OPF` кеширует модель, метод `.redact(text)` возвращает структурированный результат.

Оба способа опираются на один и тот же рантайм и читают чекпойнт из одной и той же
директории. Чекпойнт по умолчанию — `~/.opf/privacy_filter`; если его нет, при первом
запуске `opf` (или `OPF(...)`) автоматически скачивает его с Hugging Face
(`openai/privacy-filter`) через `huggingface_hub.snapshot_download`. Переопределяется
через `--checkpoint` / аргумент `model=` / переменную окружения `OPF_CHECKPOINT`.

Для нашего адаптера **выбран CLI-subprocess** (см. ниже «Принятое решение»).

## Команда/вызов

### CLI (`opf`)

```bash
# Один текст как аргумент
opf "Alice was born on 1990-01-02."

# Текст из stdin (построчно)
echo "Alice was born on 1990-01-02." | opf

# Файл
opf -f /path/to/file.txt

# Без GPU
opf --device cpu "..."

# JSON-вывод со структурированными спанами (то, что нам нужно)
opf --format json "Alice was born on 1990-01-02."
```

Полезные флаги, зафиксированные в исследовании:

| Флаг | Назначение |
|---|---|
| `--checkpoint PATH` | Путь к чекпойнту; иначе `OPF_CHECKPOINT` или `~/.opf/privacy_filter` (с автозагрузкой). |
| `--device {cpu,cuda}` | Устройство; по умолчанию `cuda`. На MVP-сервере без GPU — `cpu`. |
| `--format {text,json}` | `text` (по умолчанию) — печатает только редактированный текст; `json` — печатает структурированный JSON (нам нужен он). |
| `--output-mode {typed,redacted}` | `typed` (по умолчанию) — сохраняет категории; `redacted` — схлопывает все спаны в один общий `redacted`. **Нам нужен `typed`**, иначе потеряем типы. |
| `--decode-mode {viterbi,argmax}` | Декодер; по умолчанию `viterbi` (рекомендуемый). |
| `--json-indent N` | Отступ JSON, по умолчанию `2`. |
| `--no-print-color-coded-text` | Убрать ANSI-подсветку из вывода (нас не касается при парсинге JSON, но удобно). |

Подкоманды `eval` и `train` для MVP не нужны.

### Python API (на случай будущей миграции)

```python
from opf import OPF, RedactionResult

redactor = OPF(device="cpu", output_mode="typed")  # кеширует чекпойнт
result: RedactionResult = redactor.redact("Alice was born on 1990-01-02.")
# result.text, result.detected_spans, result.redacted_text, result.summary
```

Метод `OPF.redact` — **синхронный**, тяжёлый (PyTorch forward pass), должен вызываться
из `asyncio.run_in_executor` либо через CLI-subprocess.

## Входной формат

CLI принимает текст одним из способов:

1. **Позиционный аргумент** `opf "<text>"` — для коротких строк (shell-эскейп).
2. **stdin** — `cat file.md | opf`. ВАЖНО: при чтении из пайпа `opf` трактует stdin
   **построчно** (`iter_inputs` в `opf/_cli/args.py` делит вход по `\n`).
   Это значит, что **для многострочного текста передача через stdin — ненадёжна**:
   длинные строки без перевода каретки могут читаться как одна, но если в тексте есть
   `\n`, каждая строка уйдёт в модель **отдельным запросом**. Это не то, что нам нужно.
3. **Файл** через `-f /path/to/file.txt`. Файл читается целиком как UTF-8 и
   обрабатывается **одним запросом**. Это **предпочтительный способ** для длинных
   документов.

> Для MVP выбираем способ **«временный файл»** (`-f`): создаём временный `.txt`,
> пишем туда полный текст документа, запускаем `opf -f tmp.txt --format json --output-mode typed --device cpu`,
> читаем stdout, удаляем файл. Это устойчиво к переводам строк, спецсимволам,
> квотингу и ограничениям stdin-пайпа.

## Выходной формат

При `--format json` CLI печатает в stdout **один JSON-объект на каждый вход**
(`schema_version: 1`). В режиме `--output-mode typed` поля выглядят так:

```json
{
  "schema_version": 1,
  "summary": {
    "output_mode": "typed",
    "span_count": 3,
    "by_label": {
      "private_person": 1,
      "private_date": 2
      // ключи отсортированы лексикографически (см. build_detection_summary)
    },
    "decoded_mismatch": false
  },
  "text": "Alice was born on 1990-01-02.",
  "detected_spans": [
    {
      "label": "private_person",
      "start": 0,
      "end": 5,
      "text": "Alice",
      "placeholder": "<PRIVATE_PERSON>"
    }
  ],
  "redacted_text": "<PRIVATE_PERSON> was born on <PRIVATE_DATE>."
}
```

Контрактные гарантии (из `OUTPUT_SCHEMAS.md`):

- `schema_version` — стабильный, меняется только при ломающих изменениях. Сейчас `1`.
- `text` — нормализованный вход (может отличаться от переданного, если токенайзер
  сделал round-trip с потерями; тогда добавляется поле `warning`).
- `detected_spans[*].label` — **одна из 8 строк** (см. ниже).
- `detected_spans[*].start` / `end` — символьные офсеты в `text` (как в Python, `start` inclusive, `end` exclusive).
- `detected_spans[*].placeholder` — **дефолтный плейсхолдер модели, без номера** (`<PRIVATE_PERSON>` и т.п.). Нумерация по документу — на нашей стороне (см. «Замечания и риски»).
- `redacted_text` — текст, в котором все спаны заменены на их `placeholder` в порядке возрастания `start`.

> Дополнительные поля могут появляться аддитивно (`OUTPUT_SCHEMAS.md`: "Additive fields may appear over time, but existing keys should remain stable unless `schema_version` changes").

В режиме `--output-mode redacted` все `label` становятся строкой `"redacted"`, а
`placeholder` — общим `<REDACTED>`. **Этот режим нам не подходит** — теряются типы сущностей.

## Список типов сущностей

Дефолтный чекпойнт (`openai/privacy-filter` на Hugging Face) использует категорию
`category_version="v2"`, в которой **8 типов** (`opf/_common/label_space.py`):

```python
SPAN_CLASS_NAMES_BY_CATEGORY_VERSION["v2"] = (
    "O",                       # background, не сущность
    "account_number",
    "private_address",
    "private_date",
    "private_email",
    "private_person",
    "private_phone",
    "private_url",
    "secret",
)
```

### Сравнение с `docs/architecture.md`

| Архитектура (`EntityType`) | privacy-filter `v2` | Совпадение |
|---|---|---|
| `private_person` | `private_person` | да |
| `private_address` | `private_address` | да |
| `private_email` | `private_email` | да |
| `private_phone` | `private_phone` | да |
| `private_date` | `private_date` | да |
| `private_url` | `private_url` | да |
| `account_number` | `account_number` | да |
| `secret` | `secret` | да |

**Типы сущностей — совпадают** (8 из 8, лексикографически и по смыслу). `EntityType` править не нужно.

> В репозитории также определены более новые таксономии `v4` (15 категорий) и `v7` (25+ категорий), но они **не активированы в дефолтном чекпойнте**. Если в будущем Hugging Face-репо обновит дефолт на `v4`/`v7` — это сломает наш маппинг. См. «Замечания и риски».

## Зависимости

Из `privacy-filter/pyproject.toml` (runtime-зависимости пакета `opf`):

| Пакет | Версия | Зачем |
|---|---|---|
| `huggingface_hub` | не зафиксирована (нижняя граница отсутствует) | скачивание чекпойнта при первом запуске |
| `numpy` | не зафиксирована | массивы, токенайзер, пост-обработка |
| `packaging` | не зафиксирована | вспомогательное (версии) |
| `torch` | не зафиксирована | сама модель (1.5B параметров) |
| `safetensors` | не зафиксирована | загрузка весов чекпойнта |
| `tiktoken` | не зафиксирована | токенизация |

Плюс:

- `setuptools>=68` — только build-time.
- `tqdm` — транзитивная зависимость, импортируется `from tqdm.auto import tqdm` в `opf/_common/checkpoint_download.py` (для прогресс-бара при скачивании чекпойнта). **Формально в `pyproject.toml` не объявлена** — придёт как зависимость `huggingface_hub`.
- `python>=3.10`.

> В `pyproject.toml` submodule'а **версии не пиннованы** (нет `>=`/`==`). Реально нужные версии определяются transitive constraints `huggingface_hub`/`torch`/`safetensors` на момент установки. Для нашего backend это значит, что конкретные версии мы зафиксируем в `pyproject.toml` корневого проекта в опциональной группе `ml` (фаза 3).

**Размер модели:** 1.5B параметров, ~3 ГБ на диске (safetensors). Скачивается
один раз, кладётся в `~/.opf/privacy_filter/`.

## Принятое решение по интеграции

**Выбран CLI-subprocess** (вариант по умолчанию из спецификации фазы 1). Причины:

1. **Изоляция.** ML-зависимости (`torch`, `safetensors`, `tiktoken`, плюс сам чекпойнт ~3 ГБ) живут в отдельном venv, который ставится при `pip install -e ./privacy-filter` (или `uv pip install -e ./privacy-filter`). Backend остаётся лёгким — в его runtime-зависимостях нет `torch`.
2. **Надёжность stdin.** У `opf` пайп-режим читает вход **построчно**, что для длинных документов с переводами строк даёт несколько независимых запросов вместо одного. CLI-subprocess с временным файлом (`-f`) обходит это: `iter_inputs` в `opf/_cli/args.py` для `-f` читает файл целиком как UTF-8.
3. **Таймаут.** Через `asyncio.create_subprocess_exec(... timeout=NEIRONIR_PRIVACY_FILTER_TIMEOUT)` мы получаем жёсткую защиту event loop от подвисшего процесса.
4. **Совместимость с пакетированием.** ML-окружение можно ставить отдельным шагом, без пересборки основного backend-образа.

**Python API остаётся запасным вариантом.** Если в фазе 5+ выяснится, что
subprocess-вызов слишком дорог по оверхеду запуска (каждый запрос — старт Python
+ загрузка `torch` + загрузка чекпойнта ~3 ГБ в RAM), переключаемся на
долгоживущий `OPF(...)` в отдельном worker-процессе. Контракт JSON-ответа
совместим с обоими способами (в Python API тот же `RedactionResult.to_dict()`).

Подробности интеграции (конкретные пути, настройки, обработка ошибок) — в
`docs/agents/03-backend.md`, раздел «Интеграция с privacy-filter».

## Замечания и риски

1. **Плейсхолдеры без номера.** Модель в `detected_spans[*].placeholder` отдаёт
   `<PRIVATE_PERSON>`, `<PRIVATE_EMAIL>` и т.д. **без порядкового номера**.
   По `architecture.md` нам нужны `<PRIVATE_PERSON1>`, `<PRIVATE_PERSON2>`, …
   со сквозной нумерацией вхождений данного типа в пределах документа.
   **Решение — на стороне адаптера**: в `backend/neironir/privacy/client.py`
   мы **не используем** поле `placeholder` из выхода модели, а генерируем свои
   плейсхолдеры через `PlaceholderCounter` (фаза 2, `domain/placeholder.py`).
   `start`/`end`/`label` из выхода модели сохраняем как есть. Это не расхождение
   по типам, но критично зафиксировать.
2. **Категория таксономии — `v2`, не зафиксировано в репо.** `category_version`
   читается из `config.json` чекпойнта. Дефолт в `opf/_common/label_space.py` —
   `v2`. Если Hugging Face-репо `openai/privacy-filter` обновит чекпойнт на `v4`
   или `v7` (а они **уже определены в коде**), наш маппинг строк→`EntityType`
   сломается. **Митигация:** в адаптере после запуска CLI проверять
   `summary.output_mode` и `detected_spans[*].label` на вхождение в
   фиксированный список из 8 типов; всё, что вне списка — логировать и
   игнорировать. Это можно будет ужесточить, если появится явное поле
   `category_version` в выводе (сейчас его нет в `OUTPUT_SCHEMAS.md`).
3. **Размер модели и диск.** Чекпойнт ~3 ГБ скачивается автоматически при
   первом запуске. На сервере без интернета — нужно предварительно положить
   чекпойнт в `~/.opf/privacy_filter` (или в путь, заданный `OPF_CHECKPOINT`).
4. **stdout vs stderr.** CLI печатает summary-строку в **stderr** (латентность)
   и сам JSON — в **stdout**. Наш subprocess-клиент должен читать только stdout.
   В stderr может прийти лог скачивания чекпойнта — мы его не парсим, но
   полезно пробрасывать в `logger.debug` основного backend.
5. **CPU-режим — медленный.** В `README.md` явно сказано: модель маленькая
   (1.5B) и работает в браузере/на ноутбуке, но на CPU inference одного
   документа на 10–50 страниц — это десятки секунд. Для MVP — допустимо;
   в проде — нужен GPU или отдельный worker-pool. **Таймаут по умолчанию
   `NEIRONIR_PRIVACY_FILTER_TIMEOUT` нужно ставить с запасом** (например, 120
   секунд на документ; точную цифру уточним в фазе 3 после первых замеров).
6. **Длинный контекст (128K токенов).** Модель поддерживает 128K-токенный
   контекст, но `extract_text` для больших `.docx` может выдать текст длиннее.
   Делить на чанки в адаптере не нужно (модель сама управляет контекстом
   через `n_ctx`), но **время inference растёт линейно** с длиной. Это
   ещё один аргумент в пользу отдельного worker-процесса в будущем.
7. **Сторонние ключи.** `huggingface_hub` для скачивания чекпойнта может
   требовать аутентификации (если репо станет gated). Сейчас `openai/privacy-filter`
   публичный — аутентификация не нужна. Если это изменится — нужно будет
   завести Hugging Face-токен и прокинуть его в окружение subprocess'а
   через `HF_TOKEN` (стандартная практика `huggingface_hub`).
8. **Submodule клонировался через `github.com` напрямую** (не через алиас
   `github.com-neironir`, который указывает на наш ключ). Это сработало,
   потому что публичный репозиторий не требует специального доступа.
   При обновлении submodule'а в будущем — `git submodule update --remote`
   использует тот же URL, проблем не будет.
