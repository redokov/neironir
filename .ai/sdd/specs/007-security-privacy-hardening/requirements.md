# Feature: Security and Privacy Hardening

> Status: Approved
> Source: `code-review-report.md`, `code-review-agent-report.md`, спецификация 006
> Last Updated: 2026-11-08

## Overview

Устранить подтверждённые риски, из-за которых сетевой клиент может получить документы без авторизации, большой chunked-upload может исчерпать память, job может быть прочитан по утёкшему идентификатору, а административное regex-правило — надолго заблокировать обработку. Локальный пользователь, работающий через loopback, не должен потерять удобный browser flow.

## Business Context

- **Problem:** текущий pass-through без `Authorization` безопасен только при строгом loopback-развёртывании, но приложение может быть опубликовано на LAN; отдельные endpoints не имеют job-level доступа.
- **Users:** локальный пользователь, оператор LAN/M2M-развёртывания, администратор правил.
- **Value:** документы и исходный PII-текст не доступны анонимному сетевому клиенту; злоумышленник не может использовать upload/regex/download для DoS.
- **Success signal:** все security regression tests проходят, loopback UI остаётся рабочим, non-loopback API fail-closed.

## Goals

- Закрыть подтверждённые P1/P2/P3 находки из код-ревью без нарушения P-001…P-003.
- Сохранить совместимый loopback browser flow без обязательного ручного Bearer-токена.
- Добавить наблюдаемые, тестируемые ответы 401/413/422 вместо silent pass-through или зависания.

## Non-Goals / Out of Scope

- Multi-user/RBAC, OAuth/SSO, TLS и reverse-proxy termination.
- Распределённое хранилище сессий, очередь задач и горизонтальное масштабирование.
- Изменение upstream privacy-filter и его окон контекста.
- Reverse map исходный текст → placeholder.
- Автоматическое исправление уже существующих legacy jobs без access token; такие jobs доступны только администратору либо требуют повторной загрузки.

## User Stories

### US-001: Безопасный локальный и сетевой доступ

**As a** локальный пользователь
**I want to** использовать UI через `127.0.0.1` без ручной настройки Bearer
**So that** hardening не ломает основной сценарий.

**Acceptance Criteria:**
- [ ] loopback upload/status/download работают без `Authorization` и без ручного токена в UI;
- [ ] запрос к documents API с non-loopback client address без valid Bearer получает 401;
- [ ] malformed или invalid Bearer не превращается в anonymous pass-through.

### US-002: Изолированный доступ к job

**As a** владелец обработки
**I want to** получать и передавать job access credential
**So that** знание одного UUID не даёт доступ к исходному тексту и feedback.

**Acceptance Criteria:**
- [ ] job-scoped routes требуют access credential либо admin session;
- [ ] чужой/неверный credential даёт одинаковый 404 и не раскрывает существование job;
- [ ] frontend сохраняет credential и использует его во всех последующих запросах.

### US-003: Ограниченная обработка входа и ответа

**As a** оператор
**I want to** ограничить входной поток и крупные JSON-downloads
**So that** chunked traffic и base64 не исчерпывали память.

**Acceptance Criteria:**
- [ ] upload выше лимита получает 413 до создания job/storage artifacts;
- [ ] chunked и обычный Content-Length paths имеют одинаковый лимит;
- [ ] JSON/base64 download выше отдельного лимита получает предсказуемый 413 с рекомендацией binary download.

### US-004: Безопасные правила и сессии

**As a** администратор
**I want to** добавлять правила и завершать сессии без риска зависания или повторного доступа
**So that** админская поверхность не становится DoS-вектором.

**Acceptance Criteria:**
- [ ] опасный dynamic regex отклоняется при добавлении и не активируется при загрузке с диска;
- [ ] `/logout` отзывает текущую сессию до истечения TTL;
- [ ] новый login создаёт независимую действующую сессию.

## Functional Requirements

### FR-001: Fail-closed boundary — Must Have

WHEN запрос обращается к `/api/v1/documents/*`
IF адрес клиента не является loopback и запрос не содержит valid Bearer API key
THE SYSTEM SHALL вернуть HTTP 401 без чтения или изменения job
SO THAT documents API не будет анонимно доступен при LAN-развёртывании.

### FR-002: Loopback compatibility — Must Have

WHEN запрос к documents API приходит с loopback client address
IF отсутствует `Authorization`
THE SYSTEM SHALL сохранить текущий browser flow и разрешить запрос
SO THAT локальный UI работает без ручной передачи секрета.

### FR-003: Bounded upload — Must Have

WHEN upload передаётся с Content-Length или chunked transfer
IF фактический поток превышает configured maximum
THE SYSTEM SHALL остановить чтение и вернуть HTTP 413 до создания job
SO THAT размер запроса не может превысить лимит через отсутствие заголовка.

### FR-004: Job access — Must Have

WHEN клиент вызывает job-scoped endpoint
IF он не является admin или не предъявляет credential, выданный для этой job
THE SYSTEM SHALL вернуть HTTP 404 без раскрытия существования job
SO THAT UUID и утёкший URL не заменяют авторизацию.

### FR-005: Session revocation — Must Have

WHEN администратор выполняет logout
THE SYSTEM SHALL немедленно сделать текущую session недействительной до истечения TTL
SO THAT украденная cookie не остаётся действующей после logout.

### FR-006: Safe dynamic rules — Must Have

WHEN администратор добавляет или система загружает dynamic regex
IF pattern нарушает ограничения безопасного regex или превышает configured complexity/length budget
THE SYSTEM SHALL отклонить либо пропустить pattern с явным сообщением и не применять его
SO THAT правило не блокирует обработку документов.

### FR-007: Bounded JSON download — Should Have

WHEN клиент выбирает JSON/base64 representation
IF result exceeds configured JSON response limit
THE SYSTEM SHALL вернуть HTTP 413 и предложить binary download
SO THAT base64-ответ не создаёт неконтролируемые копии данных в памяти.

### FR-008: Corrupt span tolerance — Should Have

WHEN UI получает spans с отрицательными, выходящими за текст или пересекающимися offset'ами
THE SYSTEM SHALL нормализовать или пропустить их без exception и без HTML injection
SO THAT повреждённые annotations не ломают review UI.

## Non-Functional Requirements

### NFR-001: Security / Privacy

- Не добавлять reverse map, полный PII в логи или credential в ответы/логи.
- Auth errors не должны различать «job не существует» и «нет доступа».
- `Authorization` precedence сохраняется: invalid Bearer не маскируется session cookie.

### NFR-002: Compatibility

- Python 3.11/3.12, mock и subprocess режимы сохраняются.
- Loopback web UI остаётся доступным без ручного Bearer.
- Новые настройки имеют документированные defaults в `.env.example`.

### NFR-003: Reliability

- `ruff`, `mypy`, mock/integration tests остаются зелёными.
- Coverage не опускается ниже 70%; перед релизом нужен `make test-real`.

## Decisions

### D-001: Loopback trust boundary

**Decision:** anonymous documents flow разрешён только для loopback client address; non-loopback требует Bearer независимо от того, включены ли API keys.
**Reason:** закрывает LAN-риск fail-closed и сохраняет локальный UX.
**Impacts:** FR-001, FR-002, NFR-002.

### D-002: Job credential

**Decision:** upload выдаёт одноразово показываемый job access token; в durable metadata хранится только его digest. Admin session имеет privileged bypass.
**Reason:** это защищает anonymous loopback jobs и не требует multi-user/RBAC.
**Impacts:** FR-004, US-002.

### D-003: Regex policy

**Decision:** dynamic rules проходят консервативную длину/конструкции complexity validation; unsafe patterns не активируются. Новая внешняя regex-зависимость не добавляется в MVP.
**Reason:** минимальный dependency surface и предсказуемый отказ для admin-authored rules.
**Impacts:** FR-006, NFR-002.

## Questions

### Q-001: Нужен ли LAN browser UI без Bearer?

**Status:** answered — нет. LAN-клиенты используют M2M Bearer; browser без Bearer поддерживается только loopback.

### Q-002: Нужно ли восстанавливать доступ к legacy jobs?

**Status:** answered — нет. Legacy jobs без access digest доступны admin либо требуют повторной загрузки; автоматический insecure fallback запрещён.

## Success Metrics

| Metric | Target / Signal | Notes |
|--------|-----------------|-------|
| Anonymous non-loopback documents requests | 0 accepted | regression matrix |
| Oversized chunked uploads creating jobs | 0 | integration test |
| Dynamic unsafe patterns activated | 0 | unit + loader tests |
| Existing mock suite | 100% pass | no regression |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Reverse proxy masks client address | High | document trusted deployment boundary; fail closed when address unavailable |
| Existing frontend assumptions about job_id | Medium | add token to response and central request helper, keep URL unchanged |
| Conservative regex policy rejects valid rule | Medium | explicit 422 reason and admin can simplify pattern |
| Legacy jobs lack token | Medium | admin-only fallback and clear re-upload message |
