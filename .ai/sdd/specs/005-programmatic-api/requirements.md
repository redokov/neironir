# Feature: Programmatic (M2M) API for document anonymization

> Status: Draft
> Source: `.ai/sdd/ideas/004-programmatic-api.md`
> Scope: программный доступ внешних систем к пайплайну анонимизации через статические API-ключи; расширение существующих эндпоинтов `/api/v1/documents`

## Overview

Внешние системы (скрипты, интеграции, CI) должны уметь: загрузить `.md`/`.docx` документ → анонимизировать → забрать результат — без браузерного session/CSRF-флоу. Аутентификация — статические API-ключи (`Authorization: Bearer <token>`) из конфига/`.env`. Контракт асинхронный, как существующий: `202 + job_id` → polling статуса → получение результата двумя способами (бинарный файл и JSON с base64).

Существующий UI-флоу (session cookie + CSRF) не изменяется и продолжает работать.

## Business Context

- **Цель:** открыть сервис для машинной интеграции (M2M) без дублирования пайплайна во внешних системах.
- **Принципы:** P-001/P-002/P-003 (ключи не расширяют PII-поверхность: карта замен по-прежнему не хранится); P-014 (security outranks UX — невалидный ключ = отказ); P-010 частично расширяется осознанно (auth для M2M, но не для UI — UI уже имеет session-auth из фичи 003).
- **Сигнал ценности:** асинхронный контракт (202 + polling) уже существует — расширение переиспользует проверенный пайплайн.

## User Stories

### US-001: Анонимизация документа внешней системой

**As a** внешняя система (интеграционный сервис / скрипт)
**I want** загрузить документ `.md`/`.docx` с API-ключом, дождаться завершения и получить очищенный файл
**So that** я могу анонимизировать документы в автоматическом пайплайне без человека и браузера

**Acceptance criteria:**
- POST с `Authorization: Bearer <валидный ключ>` и multipart-файлом → `202` + Job-объект с `job_id`.
- GET статуса по `job_id` с тем же ключом → переходы `pending → processing → completed/failed`.
- GET download → бинарный очищенный файл (тот же контракт, что сейчас).

### US-002: Получение результата как JSON

**As a** внешняя система
**I want** получить очищенный документ как JSON с base64-контентом
**So that** мне не нужно сохранять временный файл — контент идёт напрямую в дальнейшую обработку

**Acceptance criteria:**
- Для завершённого job доступен эндпоинт/формат ответа с base64-контентом результата + метаданные (filename, ext, MIME-тип).
- Бинарный download остаётся доступным параллельно.

### US-003: Отказ при невалидном ключе

**As a** оператор сервиса
**I want** чтобы запросы с отсутствующим/неверным API-ключом получали 401/403 и не выполнялись
**So that** чужие системы не могли пользоваться сервисом анонимизации

### US-004: Неизменность UI-флоу

**As a** существующий UI-пользователь (session + CSRF)
**I want** чтобы мой флоу загрузки/очистки/скачивания работал как раньше
**So that** внедрение API-ключей ничего не ломает

**Acceptance criteria:**
- Существующие e2e-тесты session-флоу проходят без изменений тестов.
- Session-аутентификация и API-ключ работают на одних и тех же URL `/api/v1/documents`.

## Functional Requirements

### FR-001 — Аутентификация по Bearer-ключу — Must Have
WHEN a request to `/api/v1/documents*` carries `Authorization: Bearer <token>` where `<token>` is listed in the configured API keys
THE SYSTEM SHALL accept the request and process it like an authenticated request
SO THAT external systems can use the anonymization pipeline.

- Ключи — статический список из конфига/`.env` (pydantic-settings, префикс `NEIRONIR_`).
- Ключи не логируются (только факт «валидный/невалидный ключ», без значения ключа) — P-001/P-014.

### FR-002 — Отказ для невалидного ключа — Must Have
WHEN a request to `/api/v1/documents*` carries an absent, malformed, or unknown Bearer token
THE SYSTEM SHALL reject it with HTTP 401 (отсутствует/невалиден) / 403 (форматrecognized, но ключ неизвестен — либо единый 401, см. OPEN QUESTIONS)
SO THAT unauthorised systems cannot submit documents.

- Тело ошибки — существующий формат `ErrorResponse` (`code`, `message`).
- Поведение должно быть задокументировано в `docs/api.md`.

### FR-003 — Совместимость с session-аутентификацией — Must Have
WHEN a request to `/api/v1/documents*` carries a valid session cookie (без Authorization-заголовка)
THE SYSTEM SHALL accept it as before
SO THAT the existing UI flow keeps working unchanged.

WHEN a request carries **both** a session cookie and an `Authorization` header
THE SYSTEM SHALL apply an explicitly documented precedence rule (см. OPEN QUESTIONS Q-002)
SO THAT the session-vs-API-key conflict is deterministic and testable.

- Существующие session/CSRF-тесты не изменяются и проходят.

### FR-004 — Асинхронный контракт M2M — Must Have
WHEN an authenticated client POSTs a `.md`/`.docx` file (multipart) to `/api/v1/documents`
THE SYSTEM SHALL return `202 Accepted` with the Job object (`id`, `status`, …)
SO THAT the client can poll `GET /api/v1/documents/{job_id}` until `completed`/`failed`.

- MIME/расширение-ограничения (`400` unsupported_format) и размер-лимит (`413` file_too_large, `MAX_FILE_SIZE`) — идентичны существующим, без послаблений для M2M.
- Опциональный `output_format` — как в существующем контракте (md/docx-правила без изменений).

### FR-005 — Результат в двух форматах — Must Have
WHEN a job is `completed` and the client requests the result as JSON
THE SYSTEM SHALL return `200` with base64-encoded cleaned file content plus metadata (filename, ext, media type)
SO THAT programmatic clients avoid temp-file handling.

WHEN a job is `completed` and the client requests the binary download (existing endpoint)
THE SYSTEM SHALL return the cleaned file as today (Content-Disposition, same MIME rules)
SO THAT both delivery modes coexist.

WHEN the result is requested for a non-completed or failed job
THE SYSTEM SHALL return `409`/`404` per the existing error contract.

### FR-006 — Документация API — Must Have
THE SYSTEM SHALL document the M2M authentication (Bearer), error codes (401/403), result formats and config keys in `docs/api.md` and `.env.example`
SO THAT integrators can implement clients without reading source code.

### FR-007 — E2E-тесты на реальной модели — Must Have
THE SYSTEM SHALL include e2e tests for the M2M flow (upload with API key → poll → download binary → fetch base64 JSON) executed against the real deployed model (`subprocess` mode, marker `real_model`)
SO that the programmatic contract is validated end-to-end, not only in mock (P-008).

- Mock-режимные тесты того же флоу — в обычном CI-наборе (`make check`).

## Non-Functional Requirements

### NFR-001 — Безопасность
- API-ключи хранятся только в `.env`/окружении (`.gitignore`); не хранятся в БД (её нет), не логируются (P-001, P-014, conventions: sensitive values).
- Сравнение ключей — без timing-leak в логи; полное значение ключа не попадает в ответы ошибок.
- Session/CSRF-механизмы не ослабляются (UI продолжает требовать CSRF для unsafe-методов).

### NFR-002 — Совместимость
- Никаких изменений в существующих ответах/статус-кодах для session-пользователей; OpenAPI-схема расширяется, но не ломается.
- Аутентификация M2M не расширяется автоматически на admin/rules-эндпоинты (см. Out of Scope).

### NFR-003 — Тестируемость
- Mock-тесты ключей (валидный/невалидный/отсутствующий) — в CI; coverage ≥ 70% (P-009).
- Real-model e2e — через `make test-real` (P-008).

### NFR-004 — Операционность
- Пустой список ключей в конфиге = M2M-доступ просто отсутствует; сервис работает как сейчас (UI-only), без ошибок запуска.

## Out of Scope

- **Обратная трансформация документов** (reverse) — однонаправленное преобразование, как раньше (P-003).
- Отдельный префикс/версия API (`/api/v2`, `/machine`) — расширяем существующие `/api/v1/documents`.
- OAuth2/JWT/динамическая выдача и ротация токенов, per-key права и аудит — статические ключи, плоский список.
- Доступ M2M к admin/rules/training/feedback-эндпоинтам — только базовый documents-пайплайн (upload → poll → result), если пользователь не решит иначе (Q-003).
- Rate limiting, квоты на ключ, IP-allowlist для M2M.
- Новые входные форматы (PDF и т.п.) — лимиты `.md`/`.docx` неизменны.

## Decisions

### D-001 — Статические Bearer-ключи
**Decision:** `Authorization: Bearer <token>`, список ключей в конфиге/`.env`.  
**Reason:** утверждено пользователем; достаточно для одного локального сервиса без OAuth-инфраструктуры.  
**Source:** идея 004, Direction A/C отвергнуты пользователем.  
**Impacts:** FR-001, FR-002, NFR-001, NFR-004.

### D-002 — Асинхронный контракт 202 + polling
**Decision:** переиспользуем существующий контракт (`POST /api/v1/documents` → 202 → polling).  
**Reason:** утверждено пользователем; контракт уже существует и протестирован.  
**Impacts:** FR-004.

### D-003 — Два формата выдачи результата
**Decision:** бинарный download (существующий) + JSON с base64-контентом.  
**Reason:** утверждено пользователем; программным клиентам неудобно работать с temp-файлами.  
**Impacts:** FR-005.

### D-004 — Расширение существующих эндпоинтов
**Decision:** используем `/api/v1/documents`, без отдельного M2M-префикса.  
**Reason:** утверждено пользователем; один контракт для UI и машин.  
**Impacts:** FR-001, FR-003, NFR-002.

### D-005 — E2E на реальной модели
**Decision:** real-model e2e (subprocess-режим) обязателен для M2M-флоу.  
**Reason:** утверждено пользователем; P-008.  
**Impacts:** FR-007, NFR-003.

## Questions

- **Q-001 (open):** точная семантика 401 vs 403: единый `401` для любого невалидного ключа, или `401` (нет заголовка) + `403` (ключ известен формату, но не из списка)? Влияет на FR-002 и клиентские ретраи.
- **Q-002 (open):** правило приоритета при одновременном session-cookie и Authorization-заголовке: ключ приоритетнее? session приоритетнее? 400 «конфликт учётных данных»? Влияет на FR-003.
- **Q-003 (open):** форма JSON-выдачи: отдельный эндпоинт (`GET /{job_id}/result`) vs контент-неготиация на существующем `/download` (`Accept: application/json`)? Требование фиксирует только «оба формата доступны»; выбор — на фазе design.
- **Q-004 (open):** нужен ли CSRF-токен для Bearer-запросов к unsafe-методам? (Для чистого Bearer-аuth CSRF обычно не применим — атака строится на cookie; но правило надо зафиксировать явно.)
- **Q-005 (open):** конфигурация ключей: одна переменная со списком (разделитель?), несколько переменных, или отдельная секция? Имя(а) переменных `NEIRONIR_*` — на design-фазу.

## Glossary

- **M2M (machine-to-machine):** программный клиент без браузера и UI.
- **API-ключ:** статический секрет из конфига, передаваемый в `Authorization: Bearer`.
- **Base64-результат:** JSON-представление очищенного файла (`content_base64` + метаданные).
