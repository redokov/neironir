# Feature: Apply Feedback to Result File (FR-1)

> Status: Approved
> Source: `.ai/sdd/ideas/001-apply-feedback-to-result.md`
> Scope: UI regression — поведение «Сохранить правки в файл» + удаление неиспользуемых кнопок.

## Overview

Пользователь загружает документ, сервис возвращает очищенный файл с плейсхолдерами. В review-секции пользователь может править обнаруженные сущности (reject — ложное срабатывание, add — пропущенная сущность) и нажатием одной кнопки применить правки к `result.{md|docx}`.

Текущая реализация работает корректно на бэкенде (`POST /apply-feedback` перезаписывает `result.md`), но UI после успешного apply делает `GET /annotations` и рендерит **исходный** текст со старыми спенами — пользователь видит «прежнюю» картину и интерпретирует это как потерю правок. Дополнительно: в review-секции есть три кнопки («Всё верно», «Отправить правки», «Пропустить»), которые не нужны — они либо дублируют apply, либо только пишут feedback для обучения без обновления файла, либо закрывают секцию без действий.

## Business Context

- **Цель:** дать пользователю **одну понятную кнопку** для применения правок к итоговому файлу, и **визуально подтвердить**, что файл обновлён.
- **Сигнал ценности:** пользователь больше не теряет/не путает правки; снижается support-нагрузка.
- **Связь с принципами:**
  - P-003 (No Reverse Map): не нарушаем — `feedback.json` содержит только forward-actions (`confirm`/`reject`/`add`).
  - P-007 (Mock and Real First-Class): фикс только во frontend, режимы модели не затрагиваются.
  - P-013 (Spec Before Code): этот спек — adopt-документация + regression-fix, одобрен пользователем.

## User Stories

### US-001: Применение правок к файлу с визуальным подтверждением

**As a** пользователь сервиса (юр/аналитик/журналист)  
**I want** нажать одну кнопку «Сохранить правки в файл» и увидеть, что preview обновился и отражает новые плейсхолдеры  
**So that** я уверен, что файл перезаписан, и могу его скачать

**Acceptance Criteria:**
- [ ] После успешного `POST /apply-feedback` preview показывает **обновлённое** состояние (новые плейсхолдеры из `add`, восстановленные оригиналы из `reject`).
- [ ] Пользователь видит явный success-message: «Правки применены к итоговому файлу: N добавлено, M отклонено, K подтверждено».
- [ ] Скачивание `result.{md|docx}` возвращает файл с применёнными правками (без расхождений с preview).

### US-002: Минималистичный UI review-секции

**As a** пользователь  
**I want** видеть в review-секции только релевантные кнопки  
**So that** я не путаюсь между «Сохранить правки в файл» и «Всё верно / Отправить правки / Пропустить»

**Acceptance Criteria:**
- [ ] В review-секции остаётся **только** кнопка «Сохранить правки в файл» + (опц.) «Закрыть» для скрытия секции.
- [ ] Кнопки «Всё верно», «Отправить правки», «Пропустить» **удалены** из DOM и из JS-кода.
- [ ] Текст подсказки обновлён: «Выделите текст мышью, чтобы добавить пропущенную сущность. Нажмите на сущность, чтобы удалить ложное срабатывание. Нажмите «Сохранить правки в файл», чтобы применить правки к итоговому документу.»
- [ ] Сообщения «Спасибо! Ваши правки сохранены и будут использованы для улучшения модели» (для feedback) — удалить, так как кнопки больше нет.

## Functional Requirements

### FR-001 — UI обновляется после apply-feedback — Must Have

WHEN the user clicks «Сохранить правки в файл» and the server returns 200  
THE SYSTEM SHALL re-render the preview using the response body (new annotations / spans), not a stale GET /annotations  
SO THAT the user sees the file exactly as it will be downloaded.

### FR-002 — Кнопка apply-feedback недоступна без правок — Must Have

WHEN there are no pending actions and no spans in the original reviewData  
THE SYSTEM SHALL disable the «Сохранить правки в файл» button (или показать hint «Нет правок для применения»)  
SO THAT the user does not get a «200 OK, ничего не изменилось» UX.

### FR-003 — Удаление лишних кнопок — Must Have

THE SYSTEM SHALL remove the following UI elements and their handlers:
- `#confirm-all` (кнопка «Всё верно»)
- `#submit-feedback` (кнопка «Отправить правки»)
- `#skip-review` (кнопка «Пропустить»)
- `#feedback-success` (сообщение об успешной отправке feedback)
- `#comment-section` (textarea комментария — больше не нужен)

### FR-004 — Текст подсказки обновлён — Must Have

THE SYSTEM SHALL update the hint above the preview to clearly state that «Сохранить правки в файл» applies the changes to the result file.

### FR-005 — Регрессионный тест — Must Have

THE SYSTEM SHALL contain a test that verifies:
- After apply-feedback success, `pendingActions` for `add` and `reject` are removed (already tested in `test_frontend_bugs_regression.py:251`).
- A new static-analysis test ensures `#confirm-all`, `#submit-feedback`, `#skip-review` are NOT in `app.js` / `index.html`.
- A new static-analysis test ensures that on apply-feedback success the response body is used to update preview (or annotations are re-fetched but preview reflects applied changes).

## Non-Functional Requirements

### NFR-001 — Backward compatibility API
`POST /api/v1/documents/{id}/feedback` остаётся в API (для админки и дообучения модели). Удаляется только UI; бэкенд не трогаем.

### NFR-002 — Accessibility
- `aria-live="polite"` на `apply-success` сообщении (уже было).
- Focus management: после apply кнопка «Сохранить правки в файл» остаётся в фокусе (или focus возвращается в preview).

### NFR-003 — Privacy
Никаких изменений в PII-обработке. Fix только в UI.

## Out of Scope

- Изменения в `POST /feedback` эндпоинте (для админки он ещё нужен).
- Изменения в `POST /apply-feedback` (бэкенд работает корректно).
- Удаление `collectFeedbackActions()` / `postFeedback()` из JS (могут использоваться админкой; удалим только при уверенности — иначе оставим как мёртвый код с пометкой).
- Новые действия в `domain/feedback.py` (`replace` и т.п.) — отдельная фича.
- Визуальный редизайн review-секции.

## Decisions

### D-001 — Удалить три кнопки, оставить только «Сохранить правки в файл»
**Decision:** Удалить «Всё верно», «Отправить правки», «Пропустить» и связанные DOM-элементы + JS-обработчики.  
**Reason:** Эти кнопки либо дублируют apply, либо шлют feedback без обновления файла (UX-обман), либо закрывают секцию без действий. Пользователь явно запросил их убрать.  
**Source:** прямой запрос пользователя 2025-01-15.  
**Impacts:** FR-003, NFR-001, US-002.

### D-002 — Preview после apply рендерится из response, а не из GET /annotations
**Decision:** После успешного `applyFeedbackToFile` использовать `body.annotations` (если сервер его вернёт) или явно отметить применённые плейсхолдеры в существующем `reviewData`.  
**Reason:** Сценарий B из recon'а — `GET /annotations` возвращает старые данные, preview сбрасывается. Сервер уже знает новое состояние (он только что применил правки).  
**Source:** сценарий B (recon 2025-01-15).  
**Impacts:** FR-001, US-001.

### D-003 — Не трогать бэкенд
**Decision:** Изменения только во frontend (`app.js`, `index.html`, тесты).  
**Reason:** Бэкенд работает корректно; `apply-feedback` уже обновляет `result.md`. Менять контракт API = лишний риск.  
**Source:** recon 2025-01-15, раздел 6c.  
**Impacts:** NFR-001, Out of Scope.

## Questions

_(Все вопросы решены через D-001/D-002/D-003.)_

## Glossary

- **reviewData:** `{ text, spans }` — текущее состояние документа для preview, хранится в локальной переменной `app.js`.
- **pendingActions:** массив `add`/`reject` действий, накопленных пользователем до отправки.
- **apply-feedback:** эндпоинт `POST /api/v1/documents/{id}/apply-feedback`, перезаписывает `result.md`.
- **feedback:** эндпоинт `POST /api/v1/documents/{id}/feedback`, пишет `feedback.json` для обучения.
