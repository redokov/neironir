# SDD Index

> Дашборд состояния Spec-Driven Development в проекте `neironir`.
> Источник истины по статусам — `.status`-файлы в `.ai/sdd/specs/NNN-*`, не эта таблица.

## Upstream Handoffs

- `.ai/strategy/handoff/strategy-brief.md`: **missing** (не используется в этом проекте)

## Plan

- `.ai/sdd/PLAN.md`: **draft** — отражает текущее состояние проекта (фазы 0–5 завершены) и roadmap для будущих фич.

## Steering

- `.ai/steering/product.md` — **active** — vision, personas, value, boundaries, glossary.
- `.ai/steering/tech-stack.md` — **active** — стек, зависимости, конфигурация, CI, AD.
- `.ai/steering/conventions.md` — **active** — code style, architecture patterns, testing rules, workflow.
- `.ai/steering/principles.md` — **active** — 15 принципов (MUST/SHOULD/MAY), decision rules, review expectations.

## Ideas

| ID  | Name                                                  | Status          | Path                                              |
|-----|-------------------------------------------------------|-----------------|---------------------------------------------------|
| 001 | Обратная связь (feedback) в очищенный файл            | idea:captured   | `.ai/sdd/ideas/001-apply-feedback-to-result.md`    |
| 002 | Конвертация .docx → .md (Pandoc с fallback)           | idea:captured   | `.ai/sdd/ideas/002-docx-to-md-conversion.md`      |
| 003 | Admin UI (счётчики, feedback, дообучение)             | idea:captured   | `.ai/sdd/ideas/003-admin-ui.md`                   |

## Feature Workspace

> Источник нумерации: фактические директории под `.ai/sdd/specs/`.

| Field              | Value | Notes                                                   |
|--------------------|-------|---------------------------------------------------------|
| Next Feature ID    | 001   | Вычислять из ФС перед созданием нового спека           |
| Numbering Issues   | none  | —                                                       |

## Specs

Проект находится в **фазе adopt**: SDD-артефакты заведены для уже реализованных фич как **реверс-документация** (requirements/design/tasks/review задним числом), чтобы зафиксировать контракт и облегчить будущую работу. Создание этих артефактов — отдельная инициатива пользователя, не auto-rollout.

| ID  | Feature                                       | Status                | Requirements                      | Design                          | Tasks                          | Review                          |
|-----|-----------------------------------------------|-----------------------|-----------------------------------|---------------------------------|--------------------------------|---------------------------------|
| 001 | Apply feedback to result file (FR-1)          | review:done          | `.ai/sdd/specs/001-apply-feedback-to-result/requirements.md` | `.ai/sdd/specs/001-apply-feedback-to-result/design.md` | `.ai/sdd/specs/001-apply-feedback-to-result/tasks.md` | `.ai/sdd/specs/001-apply-feedback-to-result/review.md` |
| 002 | .docx → .md conversion (FR-2)                 | review:done          | `.ai/sdd/specs/002-docx-to-md-conversion/requirements.md` | `.ai/sdd/specs/002-docx-to-md-conversion/design.md` | `.ai/sdd/specs/002-docx-to-md-conversion/tasks.md` | `.ai/sdd/specs/002-docx-to-md-conversion/review.md` |
| 003 | Admin UI: counters, feedback, training (FR-3) | review:done          | `.ai/sdd/specs/003-admin-ui/requirements.md` | `.ai/sdd/specs/003-admin-ui/design.md` | `.ai/sdd/specs/003-admin-ui/tasks.md` | `.ai/sdd/specs/003-admin-ui/review.md` |
| 004 | Stabilize Phase 2 (mypy + tests)             | review:done          | `.ai/sdd/specs/004-stabilize-phase2/requirements.md` | `.ai/sdd/specs/004-stabilize-phase2/design.md` | `.ai/sdd/specs/004-stabilize-phase2/tasks.md` | `.ai/sdd/specs/004-stabilize-phase2/review.md` |

> Примечание: статусы выше — **план для adopt-фазы**. Реальные `.status`-файлы появятся после того, как пользователь одобрит генерацию реверс-документации. См. `.ai/sdd/PLAN.md`, секция «Adopt-фаза».

## Handoff

- `.ai/sdd/handoff/sdd-brief.md`: **missing** — генерируется после одобрения tasks или завершения review для конкретной фичи.

## Next Actions

- [ ] **Подтвердить объём adopt-фазы** с пользователем: какие из 3 завершённых фич (FR-1, FR-2, FR-3) переводить в SDD-артефакты в первую очередь.
- [ ] Сгенерировать `requirements.md` для выбранных фич (реверс-документация из `docs/agents/03-backend.md` + кода).
- [ ] Сгенерировать `design.md` и `tasks.md` по тем же источникам.
- [ ] Сгенерировать `review.md` против существующих тестов (`make test-cov`).
- [ ] Перед любой новой фичей — `/skill:sdd-idea` для обсуждения.

## Workflow

- Инициализация: `/skill:sdd-init` ✅ (этот прогон)
- Идея: `/skill:sdd-idea`
- План: `/skill:sdd-plan`
- Требования: `/skill:sdd-prd`
- Дизайн: `/skill:sdd-spec`
- Таски: `/skill:sdd-tasks`
- Исполнение: `/skill:sdd-exec`
- Ревью: `/skill:sdd-review`
- Статус: `/skill:sdd-status`
