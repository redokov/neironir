# Idea: .docx → .md Conversion

> Status: idea:captured
> Created: 2025-01-15
> Source: `TODO.md` (элемент «Конвертация .docx → .md»), `docs/agents/03-backend.md`

## Raw Idea

При загрузке `.docx` пользователь может поставить чекбокс «Результат в MD-формате» и получить вместо `.docx` — файл `.md` с сохранённой структурой (заголовки, списки, выделение). Если Pandoc недоступен — fallback на плоский текст через `python-docx`.

## Problem Space

- `.docx`-конвертер MVP сохраняет только параграфы (P-012). Структура (заголовки, списки, **жирный**/*курсив*) теряется.
- Для многих пользователей (юр/аналитик) Markdown — более удобный формат для последующей работы с LLM.
- Загрузка в LLM обычно идёт в plain-text или markdown; `.docx` после очистки — лишний шаг.

## Target Users

- **Автор документа**, который предпочитает Markdown как формат для LLM.
- **Юрист/аналитик**, получающий длинные `.docx` отчёты и хочет сразу работать с `.md` (проще grep, diff, version control).

## Current Alternatives

- Конвертировать `.docx → .md` вручную через Pandoc или другой инструмент → лишний шаг.
- Получить очищенный `.docx` и жить с потерей форматирования.
- Использовать `python-docx` напрямую — даёт плоский текст, без структуры.

## Desired Outcome

- Чекбокс «Результат в MD-формате» в UI при загрузке `.docx`.
- При включённом чекбоксе сервис:
  - Пытается использовать `pandoc` для конвертации.
  - При отсутствии `pandoc` — fallback на `python-docx` (плоский текст в `.md`-обёртке).
- Имя выходного файла — `<name>.md` вместо `<name>.docx`.
- Скачивание — через `Content-Disposition` с правильным расширением и MIME-типом.

## Possible Directions

### Direction A: Pandoc-only

- **Description:** Требовать `pandoc` на машине; документировать в quickstart. Без fallback.
- **Pros:** Лучшее качество конвертации (структура сохраняется полностью).
- **Cons:** Доп. зависимость; если не установлен — фича не работает; противоречит «локальности» (нужно что-то ставить).

### Direction B: Pandoc + python-docx fallback

- **Description:** Сначала пробуем `pandoc`; при `FileNotFoundError` — fallback на `python-docx` (плоский текст в `.md`).
- **Pros:** Работает «из коробки»; качество лучше когда `pandoc` есть.
- **Cons:** Два пути кода; нужно тестировать оба.
- **Risks:** Несовпадение результата (структура vs плоский) — UX-неожиданность. Нужно явно сообщать, какой путь сработал (warning в логе / meta в `job.json`).

### Direction C: Только python-docx (текущий MVP-конвертер)

- **Description:** Просто оборачиваем извлечённый текст в `.md`-файл, без структуры.
- **Pros:** Нет внешних зависимостей; предсказуемо.
- **Cons:** Структура теряется (как в MVP-`.docx`-конвертере); фича не даёт много пользы.

## Open Questions

- **Q-001 (направление):** По `TODO.md` выбрано B (Pandoc + fallback). Подтвердить.
- **Q-002 (UI):** где разместить чекбокс? Возле drop-zone, отдельный step после upload, или в настройках профиля?
- **Q-003 (метаданные):** сохранять ли в `job.json` информацию о пути конвертации (`pandoc` / `fallback`) для аудита? Помогает в support.
- **Q-004 (для .md → .docx):** симметричная фича? Сейчас вне scope.
- **Q-005 (для Pandoc-параметров):** использовать дефолтные аргументы или дать пользователю выбор (e.g. wrap/unwrap, reference-links)?

## Constraints

- **Timeline:** реализовано в post-MVP; сроки не зафиксированы.
- **Budget:** без дополнительных затрат.
- **Technology:** см. `.ai/steering/tech-stack.md` — backend, `pypandoc` или `subprocess` вызов `pandoc`, fallback на `python-docx`.
- **Team:** текущая команда.
- **Existing product constraints:** P-011 (Native Format In, Native Format Out) — но это **исключение**, явно опциональное; P-001, P-002, P-003 — без изменений.

## Signals of Value

- `TODO.md` — фича отмечена `[x]` (завершена).
- Упомянута в `docs/api.md` как флаг/параметр API.
- UX-исследование (не задокументировано) — пользователи просили markdown-выход.

## Recommendation

- [ ] Drop
- [x] Keep exploring
- [ ] Create PLAN
- [x] **Create REQUIREMENTS directly** (фича уже реализована, adopt-фаза)

**Reason:** Реализована, требует реверс-документации. Целевой артефакт — `.ai/sdd/specs/002-docx-to-md-conversion/`.

## Notes

- **ADOPT-2025-01-15:** фактическая реализация **отказалась от pandoc** — используется собственный конвертер `backend/neironir/converters/docx_to_md.py` (python-docx, без pandoc). Причина: pandoc-шум ломал аннотации. Зафиксировано в `.ai/sdd/specs/002-docx-to-md-conversion/` (D-001, TD-001). Спек: `requirements.md` → `design.md` → `tasks.md` → `review.md` (status: `review:done`).
- Q-001 (направление) пересмотрен реализацией: выбран «собственный конвертер», а не Pandoc+fallback.
- Q-002 (UI): чекбокс размещён в upload-options, checked по умолчанию, выбор сохраняется (dataset.userSet).
- Q-003 (метаданные): путь конвертации НЕ сохраняется в job.json — конвертация теперь всегда через `convert_to_markdown()` (единый путь).
