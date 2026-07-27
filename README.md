# neironir

Веб-сервис для удаления персональных данных из текстовых документов (`.md`, `.docx`) перед загрузкой во внешние нейросети.

## Назначение

Пользователь загружает документ в сервис. Сервис через локально развёрнутую модель [privacy-filter](https://github.com/openai/privacy-filter.git) заменяет в тексте сущности восьми типов на шаблоны:

| Тип сущности | Шаблон |
|---|---|
| Имя (private_person) | `<PRIVATE_PERSON1>` |
| Адрес (private_address) | `<PRIVATE_ADDRESS1>` |
| Email (private_email) | `<PRIVATE_EMAIL1>` |
| Телефон (private_phone) | `<PRIVATE_PHONE1>` |
| Дата (private_date) | `<PRIVATE_DATE1>` |
| URL (private_url) | `<PRIVATE_URL1>` |
| Номер счёта (account_number) | `<ACCOUNT_NUMBER1>` |
| Секрет/пароль (secret) | `<SECRET1>` |

Пользователь получает очищенный документ и карту замен (orig → placeholder), чтобы при необходимости вернуть данные обратно.

## Поддерживаемые форматы

- Markdown (`.md`)
- Microsoft Word (`.docx`)

## Структура

```
neironir/
├── backend/
│   └── neironir/
│       ├── __init__.py
│       ├── main.py            # FastAPI app factory
│       └── config.py          # pydantic-settings
├── tests/                     # pytest
├── docs/                      # Проектная документация
├── pyproject.toml             # uv/PEP 621
├── uv.lock
├── .gitignore
├── .python-version
└── README.md
```

> Полная структура (с `frontend/`, `privacy-filter/`, подкаталогами `backend/`) — в [docs/architecture.md](./docs/architecture.md).

## Статус

Фаза 0 (bootstrap) завершена. См. [docs/agents/00-bootstrap.md](./docs/agents/00-bootstrap.md).
