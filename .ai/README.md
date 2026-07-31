# .ai — AI-assisted Spec-Driven Development workspace

Эта директория содержит **persistent AI-контекст** и **SDD-артефакты** для проекта `neironir`.

## Структура

```text
.ai/
├── README.md                          # этот файл
├── steering/                          # переиспользуемый контекст для любых AI-сессий
│   ├── product.md                     # vision, personas, value, scope, glossary
│   ├── tech-stack.md                  # стек, зависимости, конфигурация, CI
│   ├── conventions.md                 # code style, architecture, tests, workflow
│   └── principles.md                  # MUST/SHOULD/MAY правила, decision rules
└── sdd/                               # Spec-Driven Development
    ├── INDEX.md                       # дашборд статусов фич
    ├── PLAN.md                        # roadmap и adopt-план
    ├── ideas/                         # захваченные идеи (вход в /skill:sdd-idea)
    │   ├── 001-apply-feedback-to-result.md
    │   ├── 002-docx-to-md-conversion.md
    │   └── 003-admin-ui.md
    ├── specs/                         # по одному каталогу на фичу
    │   └── NNN-feature-slug/          # requirements.md, design.md, tasks.md, review.md, .status
    └── handoff/                       # sdd-brief.md для downstream-агентов
```

## Как это использовать

### Для AI-агента (Claude/Copilot/etc.)

1. **В начале сессии** прочитать:
   - `.ai/steering/product.md` — понять, что за продукт.
   - `.ai/steering/tech-stack.md` — стек и конфигурация.
   - `.ai/steering/conventions.md` — как писать код и тесты.
   - `.ai/steering/principles.md` — MUST/SHOULD правила.
2. **Перед новой фичей** — `.ai/sdd/INDEX.md` и `.ai/sdd/PLAN.md`.
3. **При работе с конкретной фичей** — `.ai/sdd/specs/NNN-*/`.

### Для человека

- **Перед изменением публичного API** — открыть `principles.md` (особенно P-001…P-006).
- **Перед добавлением зависимости** — открыть `tech-stack.md` (Constraints).
- **Перед стартом фичи** — `/skill:sdd-idea` → `sdd-prd` → `sdd-spec` → `sdd-tasks` → `sdd-exec` → `sdd-review`.

## Что НЕ должно попадать в .ai/

- **Секреты** (`.env`, токены) — хранятся в `.env` / Vault.
- **Артефакты сборки** — `.venv/`, `dist/`, `htmlcov/` — в `.gitignore`.
- **Большие бинарники** — модели, датасеты — в `.gitignore`.
- **Временные заметки** отдельных сессий — `notes/` (опционально, в `.gitignore`).

## Соглашения

- **Язык:** русский (как и остальной проект). Идентификаторы, статусы, команды — в EN.
- **Версионирование:** `.ai/` коммитится (кроме `.ai/sdd/handoff/*` если они содержат user-specific данные — на усмотрение).
- **Идемпотентность:** повторный запуск `sdd-init` не должен перезаписывать существующие артефакты без явного одобрения.
- **Принцип "Don't surprise the user":** все изменения `.ai/` обсуждаются до записи.

## Связанные документы

- Корень проекта: [`../README.md`](../README.md)
- Архитектура: [`../docs/architecture.md`](../docs/architecture.md)
- API: [`../docs/api.md`](../docs/api.md)
- Acceptance: [`../docs/acceptance-criteria.md`](../docs/acceptance-criteria.md)
- Агенты (фазы): [`../docs/agents/`](../docs/agents/)
- TODO: [`../TODO.md`](../TODO.md)
