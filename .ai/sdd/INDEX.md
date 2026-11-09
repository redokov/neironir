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
| 004 | Programmatic (M2M) API                                  | idea:captured   | `.ai/sdd/ideas/004-programmatic-api.md`           |
| 005 | Убрать `?access_token=` query-фолбэк (утечка токена в URL) | idea:adopted | `.ai/sdd/ideas/005-remove-access-token-query-fallback.md` |
| 006 | Персистентный реестр отозванных jti                  | idea:captured | `.ai/sdd/ideas/006-persistent-jti-revocation.md` |

## Feature Workspace

> Источник нумерации: фактические директории под `.ai/sdd/specs/`.

| Field              | Value | Notes                                                   |
|--------------------|-------|---------------------------------------------------------|
| Next Feature ID    | 009   | Вычислять из ФС перед созданием нового спека           |
| Numbering Issues   | none  | —                                                       |

## Specs

Проект находится в **фазе adopt**: SDD-артефакты заведены для уже реализованных фич как **реверс-документация** (requirements/design/tasks/review задним числом), чтобы зафиксировать контракт и облегчить будущую работу. Создание этих артефактов — отдельная инициатива пользователя, не auto-rollout.

| ID  | Feature                                       | Status                | Requirements                      | Design                          | Tasks                          | Review                          |
|-----|-----------------------------------------------|-----------------------|-----------------------------------|---------------------------------|--------------------------------|---------------------------------|
| 001 | Apply feedback to result file (FR-1)          | review:done          | `.ai/sdd/specs/001-apply-feedback-to-result/requirements.md` | `.ai/sdd/specs/001-apply-feedback-to-result/design.md` | `.ai/sdd/specs/001-apply-feedback-to-result/tasks.md` | `.ai/sdd/specs/001-apply-feedback-to-result/review.md` |
| 002 | .docx → .md conversion (FR-2)                 | review:done          | `.ai/sdd/specs/002-docx-to-md-conversion/requirements.md` | `.ai/sdd/specs/002-docx-to-md-conversion/design.md` | `.ai/sdd/specs/002-docx-to-md-conversion/tasks.md` | `.ai/sdd/specs/002-docx-to-md-conversion/review.md` |
| 003 | Admin UI: counters, feedback, training (FR-3) | review:done          | `.ai/sdd/specs/003-admin-ui/requirements.md` | `.ai/sdd/specs/003-admin-ui/design.md` | `.ai/sdd/specs/003-admin-ui/tasks.md` | `.ai/sdd/specs/003-admin-ui/review.md` |
| 004 | Stabilize Phase 2 (mypy + tests)             | review:done          | `.ai/sdd/specs/004-stabilize-phase2/requirements.md` | `.ai/sdd/specs/004-stabilize-phase2/design.md` | `.ai/sdd/specs/004-stabilize-phase2/tasks.md` | `.ai/sdd/specs/004-stabilize-phase2/review.md` |
| 005 | Programmatic (M2M) API                       | review:done          | `.ai/sdd/specs/005-programmatic-api/requirements.md` | `.ai/sdd/specs/005-programmatic-api/design.md` | `.ai/sdd/specs/005-programmatic-api/tasks.md` | `.ai/sdd/specs/005-programmatic-api/review.md` |
| 006 | Code Review Fixes (opus + deepseek cross-review) | review:done | — (источник: `code-review-agent-report.md`) | — | `.ai/sdd/specs/006-code-review-fixes/tasks.md` (T1–T17, коммит `ff325dc`) | `.ai/sdd/specs/006-code-review-fixes/review.md` |
| 007 | Security and Privacy Hardening | review:done | `.ai/sdd/specs/007-security-privacy-hardening/requirements.md` | `.ai/sdd/specs/007-security-privacy-hardening/design.md` | `.ai/sdd/specs/007-security-privacy-hardening/tasks.md` | `.ai/sdd/specs/007-security-privacy-hardening/review.md` |
| 008 | Remove `?access_token=` query fallback | review:done | `.ai/sdd/specs/008-remove-access-token-query-fallback/requirements.md` | `.ai/sdd/specs/008-remove-access-token-query-fallback/design.md` | `.ai/sdd/specs/008-remove-access-token-query-fallback/tasks.md` | `.ai/sdd/specs/008-remove-access-token-query-fallback/review.md` |

> Примечание: статусы выше — **план для adopt-фазы**. Реальные `.status`-файлы появятся после того, как пользователь одобрит генерацию реверс-документации. См. `.ai/sdd/PLAN.md`, секция «Adopt-фаза».

## Handoff

- `.ai/sdd/handoff/sdd-brief.md`: **active** — обзорное состояние после review спеки 005 (Programmatic (M2M) API, Approved with follow-ups).

## Next Actions

- [x] **Спека 007 (security hardening)** — implementation:done, review passed
      (mock-гейты: ruff/mypy/node, pytest 601/0/28, coverage 89.58%; коммит `251eb7d`).
- [x] **Релиз v0.2.0** — выпущен 2026-11-09 (тег `v0.2.0`). Блокер P-008 снят:
      `real_model`-сьюты переведены на контракты 007 (loopback-фикстура +
      `X-Job-Token`), `make test-real` — 7/0.
- [x] Документация синхронизирована с контрактами 007: README/architecture
      (коммит `8f536c7`) и `docs/api.md` — loopback-граница в «Правилах доступа»,
      раздел про API правил и контракт `unsafe_regex`, лимит JSON-выдачи
      (`result_too_large`) и его переменная в таблице конфигурации.
- [x] **Спека 008 (query-фолбэк токена)** — review passed: header-only контракт
      (`verify_job_access`), fetch-скачивание в UI (Blob/createObjectURL),
      ломающее изменение в CHANGELOG `[Unreleased]`. Гейты: ruff/mypy/node,
      mock 604/0, real 7/0.
- [ ] Идея 006 (персистентный jti-реестр) — обсудить с пользователем перед
      взятием в спеку.

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
