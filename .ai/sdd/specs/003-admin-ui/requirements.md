# Feature: Admin UI (F11)

> Status: Approved (adopt-реверс-документация, 2025-01-15)
> Source: `.ai/sdd/ideas/003-admin-ui.md`
> Scope: уже реализованная фича; фиксация контракта по фактическому коду

## Overview

Веб-интерфейс для администратора системы: статистика обработки, drill-down по документам с feedback, запуск/остановка дообучения `opf train`, управление runtime-настройками и предложенными правилами. Защищён сессионной аутентификацией + CSRF.

**Расхождение с идеей 003:** в идее Q-002 стоял вопрос про auth-стратегию («IP-allowlist, отдельный порт, basic-auth»). Фактически реализована **полноценная сессионная аутентификация** (cookies + CSRF), один admin-пользователь из `.env` (`NEIRONIR_ADMIN_USER`/`NEIRONIR_ADMIN_PASSWORD`). Это superset идеи.

## Business Context

- **Цель:** дать оператору возможность управлять системой через UI, а не CLI: счётчики, правки пользователей (feedback), дообучение, правила, настройки.
- **Принципы:** P-001 (админ не видит исходный PII-текст в списках — только метаданные и drill-down с текстом для верификации правок), P-014 (security outranks UX → auth обязательна), P-010 (один процесс — admin в том же FastAPI).
- **Сигнал ценности:** feedback-данные превращаются в обучающий датасет одним кликом; правила модерируются через UI.

## User Stories

### US-001: Просмотр статистики

**As a** администратор  
**I want** видеть счётчики обработанных документов (всего / completed / failed / с feedback) с разбивкой по периодам  
**So that** я понимаю загрузку системы

### US-002: Drill-down по документам с feedback

**As a** администратор  
**I want** видеть список документов с feedback и подробности по каждому (job-метаданные, извлечённый текст, annotations, feedback-actions)  
**So that** я могу оценить качество правок пользователей

### US-003: Запуск дообучения модели

**As a** администратор  
**I want** запускать `opf train` на накопленных аннотациях из UI, видеть статус (эпоха, loss, ETA) и останавливать  
**So that** модель дообучается без доступа к серверу

### US-004: Управление предложенными правилами

**As a** администратор  
**I want** видеть предложенные правила, одобрять/отклонять их, добавлять ручные  
**So that** система обучается на экспертной модерации

### US-005: Runtime-настройки

**As a** администратор  
**I want** менять таймаут privacy-filter через UI  
**So that** не нужно редактировать .env и перезапускать сервер

## Functional Requirements

### FR-001 — Статистика — Must Have
WHEN an admin requests `GET /api/v1/admin/stats?period=…&days=…`
THE SYSTEM SHALL return total/completed/failed/feedback counters and per-period buckets
SO THAT the dashboard shows load over time.

- period ∈ {day, week, month}; days ∈ [1, 365]; invalid → 422.

### FR-002 — Список и drill-down документов — Must Have
WHEN an admin requests `GET /api/v1/admin/documents?limit=N` (≤500)
THE SYSTEM SHALL return job summaries with feedback statistics (confirmed/rejected/added, corrections_by_type, false_positive_by_type, missed_by_type)
SO THAT the operator can triage feedback.
WHEN `GET /api/v1/admin/documents/{job_id}` is requested
THE SYSTEM SHALL return job metadata + extracted text + annotations + feedback; 404 if missing.

### FR-003 — Дообучение — Must Have
WHEN `POST /api/v1/admin/training/start?epochs=E` is called
THE SYSTEM SHALL build a JSONL dataset from `training_dataset.jsonl`/feedback and spawn `opf train` as subprocess
SO THAT training runs in background.
- 409 `training_in_progress` if already running; 422 `training_failed` on startup error.
- `GET /training/status` — snapshot of TrainingState (idle/running, epoch, loss, ETA).
- `POST /training/stop` — SIGTERM subprocess, idempotent when idle.

### FR-004 — Правила — Must Have
THE SYSTEM SHALL expose `/api/v1/rules`:
- `GET` — list rules with metadata
- `GET /stats` — rule statistics
- `POST /proposals` — generate proposals
- `POST /{rule_id}/approve` — approve
- `POST /{rule_id}/reject` — reject
- `POST` — add manual rule

### FR-005 — Runtime-настройки — Should Have
THE SYSTEM SHALL expose `GET/PUT /api/v1/admin/settings` (privacy_filter_timeout, persisted to storage).

### FR-006 — Аутентификация и CSRF — Must Have
WHEN a request hits any `/api/v1/admin/*` or `/api/v1/rules` endpoint
THE SYSTEM SHALL require a valid admin session (cookie) and, for unsafe methods, a matching CSRF token
SO THAT only the admin can change system state.
- `POST /login` (form, admin user/password from settings) sets session + CSRF cookies.
- `POST /logout` clears them.
- `GET /api/v1/auth/whoami` — current user (401 if not authed).
- Middleware: max body size, secure-cookie handling, origin checks.

### FR-007 — Admin UI страницы — Must Have
THE SYSTEM SHALL serve `GET /admin` → `frontend/admin.html` (vanilla JS SPA: stats, documents, training, rules, settings, logout).

### FR-008 — Тесты — Must Have
THE SYSTEM SHALL include tests for stats, documents, training (start/status/stop/409), auth (login/logout/whoami/401), CSRF, rules, admin-UI serving.

## Non-Functional Requirements

### NFR-001 — Безопасность
- Session cookie: HttpOnly, Secure (в prod), SameSite; CSRF-токен для unsafe методов.
- Пароль admin — из env, не хардкод, не в логах.
- P-001: списки документов НЕ включают полный исходный текст (только сводки); полный текст — только в drill-down по конкретному job (для верификации правок).

### NFR-002 — Идемпотентность/конкурентность
- Два параллельных `training/start` — второй получает 409.
- `training/stop` когда idle — не падает.

### NFR-003 — Производительность
- Stats-вычисление по FS — в пределах десятков мс для сотен job.

### NFR-004 — Совместимость
- Admin-API отдельно от user-API (`/api/v1/admin/*`); удаление `/api/v1/admin/*` из openapi-схемы для неавторизованных не требуется, но эндпоинты защищены.

## Out of Scope

- Мультипользовательские роли (только один admin).
- Возврат исходного PII в список документов (drill-down — исключение для верификации).
- Метрики/мониторинг/alerting.
- Версионирование чекпоинтов модели с откатом.

## Decisions

### D-001 — Сессионная auth вместо basic/IP-allowlist
**Decision:** Cookies (HttpOnly) + CSRF-токен; один admin-пользователь из env.  
**Reason:** Безопаснее basic-auth, не требует отдельного порта; достаточен для одного оператора.  
**Source:** факт. код `auth/`, `admin/router.py` deps.  
**Impacts:** FR-006, NFR-001.

### D-002 — Training через subprocess `opf train`
**Decision:** `admin/training.py` строит JSONL-датасет из feedback и запускает `opf train` subprocess с общим `TrainingState`.  
**Reason:** in-process API `opf` — вне scope; subprocess изолирует падения.  
**Source:** `admin/training.py`; `_opf_cmd()` в router.  
**Impacts:** FR-003, NFR-002.

### D-003 — Правила в отдельном роутере `/api/v1/rules`
**Decision:** CRUD-правил в `api/rules.py` (approve/reject/proposals/manual).  
**Reason:** отдельная сущность от job-жизненного цикла; переиспользуется модерацией.  
**Source:** `api/rules.py`.  
**Impacts:** FR-004.

### D-004 — Drill-down показывает текст (осознанное исключение P-001)
**Decision:** `GET /admin/documents/{job_id}` возвращает извлечённый текст + annotations + feedback.  
**Reason:** оператор должен верифицировать правки. Списки (`/documents`) — только сводки.  
**Source:** `router.get_document_detail`.  
**Impacts:** FR-002, NFR-001.

## Questions

_(Решены через D-001…D-004; Q-002 идеи 003 — фактически выбран session auth.)_

## Glossary

- **JobFeedbackSummary:** сводка по job с feedback-статистикой (`admin/stats.py`).
- **TrainingState:** общий снапшот состояния обучения (idle/running, epoch, loss, eta).
- **ProposedRule:** предложенное правило для модерации (`api/rules.py`).
- **fetchCsrf:** обёртка fetch в `admin.js`, добавляющая CSRF-токен и редирект на `/login` при 401.
