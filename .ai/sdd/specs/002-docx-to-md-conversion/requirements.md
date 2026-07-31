# Feature: .docx → .md Conversion (F10)

> Status: Approved (adopt-реверс-документация, 2025-01-15)
> Source: `.ai/sdd/ideas/002-docx-to-md-conversion.md`
> Scope: уже реализованная фича; фиксация контракта по фактическому коду

## Overview

При загрузке `.docx` пользователь может запросить результат в формате Markdown (чекбокс «Результат в MD-формате» в UI / параметр `output_format=md` в API). Сервис конвертирует `.docx` в markdown, применяет анонимизацию к полученному тексту и отдаёт `result.md`.

**Важное расхождение с идеей:** идея 002 описывала «Pandoc с fallback на python-docx». Фактическая реализация — **собственный конвертер** `backend/neironir/converters/docx_to_md.py` на `python-docx`, без вызова `pandoc` (см. docstring модуля: «we never run pandoc»). Причина: pandoc вносил шум (`.mark`/`.underline` span-classes, `{=html}`-блоки, autolink-обёртки), ухудшавший качество аннотаций. Реверс-документация фиксирует фактическое поведение.

## Business Context

- **Цель:** дать пользователю `.md`-результат с сохранением структуры (заголовки, списки, выделение, таблицы) — удобно для загрузки в LLM.
- **Принципы:** P-011 (Native Format In/Out) — с явным опциональным исключением docx→md; P-012 (лимиты задокументированы).
- **Сигнал ценности:** без конвертации `.docx` теряет структуру (плоский текст); с конвертацией пользователь получает структурированный `.md`.

## User Stories

### US-001: Загрузка .docx с результатом в Markdown

**As a** пользователь (юр/аналитик/журналист)  
**I want** при загрузке `.docx` получить очищенный файл в формате `.md` с сохранёнными заголовками, списками, выделением и таблицами  
**So that** я могу использовать результат в markdown-пайплайне без потери структуры

**Acceptance Criteria:**
- [ ] При `output_format=md` и source `.docx` результат сохраняется как `result.md`.
- [ ] Заголовки (`Заголовок 1..N`, `1 уровень`, `2 уровень`) рендерятся как `#`, `##`, ….
- [ ] `**bold**` и `*italic*` сохраняются; underline/strikethrough/custom-styles — сбрасываются в plain text.
- [ ] Таблицы рендерятся как pipe-tables (первая строка — header).
- [ ] Гиперссылки схлопываются в plain text (без `[label](url)`).
- [ ] `md → docx` НЕ поддерживается (400).

### US-002: Apply-feedback работает с docx→md

**As a** пользователь  
**I want** применять правки (reject/add) к документу, загруженному как `.docx` с `output_format=md`  
**So that** я получаю корректный `result.md` после правок

**Acceptance Criteria:**
- [ ] `POST /apply-feedback` работает для job с `output_ext=md` (в т.ч. исходный `.docx`).
- [ ] Для job с `output_ext=docx` apply-feedback возвращает 400 с подсказкой «Re-upload with output_format=md» (бэкенд-ограничение, не баг).

## Functional Requirements

### FR-001 — Конвертация docx→md — Must Have

WHEN a `.docx` file is uploaded WITH `output_format=md`  
THE SYSTEM SHALL convert the document to markdown via `converters/docx_to_md.convert_to_markdown()`  
THEN SHALL annotate the markdown text with the privacy filter  
SO THAT the output `result.md` keeps headings, emphasis, and pipe-tables.

### FR-002 — Валидация комбинаций форматов — Must Have

WHEN the requested conversion is unsupported (`md → docx`, or unknown format)  
THE SYSTEM SHALL reject with HTTP 400 `unsupported_output_format`  
SO THAT users get a clear error instead of a wrong file.

### FR-003 — По умолчанию формат результата = формат источника — Must Have

WHEN `output_format` is not provided  
THE SYSTEM SHALL use the source extension (`docx` → `docx`, `md` → `md`)  
SO THAT round-trip behaviour stays default.

### FR-004 — UI чекбокс «Результат в MD-формате» — Must Have

THE SYSTEM SHALL expose a checkbox in the upload UI that sends `output_format=md`
- checked by default for `.docx` uploads (и для `.md` — безвреден, identity)
- user choice survives across uploads (dataset-флаг `userSet`)

### FR-005 — Аннотации привязаны к markdown-тексту — Must Have

WHEN conversion is `docx → md`  
THE SYSTEM SHALL extract text via the markdown converter (not via the flat docx extractor)  
SO THAT offsets used by privacy-filter and apply-feedback match the markdown representation.

### FR-006 — Тесты — Must Have

THE SYSTEM SHALL include tests for:
- unit: headings, bold/italic, tables, hyperlinks, whitespace collapse, empty docx
- integration: upload docx with output_format=md → completed → download .md
- integration: apply-feedback on docx with output_ext=md
- contract: md→docx → 400, invalid format → 400

## Non-Functional Requirements

### NFR-001 — Совместимость
`convert_to_markdown` не должен ломать offset-модель: извлечённый текст — ровно то, что аннотируется и что возвращает `GET /annotations`.

### NFR-002 — Производительность
Конвертация одного документа — в пределах секунд (python-docx in-process; без subprocess).

### NFR-003 — Безопасность / приватность
Конвертер не должен сохранять исходный текст в лог; только в `extracted_text.txt` внутри job-каталога (как и для других форматов).

## Out of Scope

- `md → docx` (явно 400).
- Pandoc как рантайм-зависимость (заменён собственным конвертером).
- Сохранение inline-стилей Word (underline, strike, custom) — сбрасываются.
- Nested tables — не поддерживаются (как и в docx-конвертере MVP).

## Decisions

### D-001 — Собственный конвертер вместо pandoc
**Decision:** Использовать `converters/docx_to_md.py` (python-docx) вместо pandoc CLI.  
**Reason:** pandoc добавлял шум в аннотации (`.mark`/`.underline` classes, `{=html}`, autolink-обёртки); собственный конвертер даёт чистый предсказуемый markdown-подмножество.  
**Source:** фактический код; docstring `docx_to_md.py`.  
**Impacts:** FR-001, FR-005, FR-006, Out of Scope.

### D-002 — Подмножество markdown
**Decision:** Рендерить только `#`-заголовки, `**bold**`, `*italic*`, pipe-tables; остальное — plain text.  
**Reason:** Достаточно для целей фичи; минимизирует шум для модели.  
**Source:** docstring `docx_to_md.py`.  
**Impacts:** FR-001, FR-006.

### D-003 — Гиперссылки → plain text
**Decision:** Не эмитировать `[label](url)`, а схлопывать в текст ссылки.  
**Reason:** Word-автоссылки (email/URL) иначе шумят в аннотациях; PII-детекция работает по тексту.  
**Source:** `test_hyperlink_collapses_to_text`, `test_no_autolink_wrappers`.  
**Impacts:** FR-001, FR-006.

## Questions

_(Решены через D-001…D-003; Q-001 идеи 002 пересмотрен фактической реализацией.)_

## Glossary

- **output_format:** Form-параметр API (`md` / `docx`), валидируется `_validate_output_format`.
- **convert_to_markdown():** `backend/neironir/converters/docx_to_md.py` — основной конвертер.
- **MarkdownElement:** dataclass-итератор элементов (heading/paragraph/blank/table).
- **effective_output_ext:** итоговое расширение результата (`job.effective_output_ext`).
