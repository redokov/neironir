# Feature: Stabilize Phase 2 (mypy + pre-existing test failures)

> Status: Approved (2025-01-15)
> Source: OD-003 (стабилизация Phase 2); review-следы 001–003; handoff-блокеры
> Scope: закрыть 19 mypy-ошибок + 9 pre-existing падений тестов

## Overview

Phase 2 зафиксирована как реализованная, но на baseline есть технический долг:
- 19 mypy-ошибок в `backend/` (dict type-args, unused ignore, no-untyped-def, call-overload).
- 9 pre-existing падений тестов (e2e чекбокс/banner/toolbar, admin-html serve redirect loop, whoami, settings).

Цель — зелёный `make check` (lint + type + test) и, где возможно, починенные тесты, чтобы релиз-качество Phase 2 было подтверждено.

## Requirements

### FR-001 — mypy чисто — Must Have
`mypy backend/neironir` → 0 ошибок.

### FR-002 — pre-existing падения проанализированы — Must Have
Каждое из 9 падений: либо починено (тест+код), либо обоснованно отмечено как skipped/исправлен тест, либо задокументировано с причиной.

### FR-003 — Регрессии не добавляются — Must Have
Полный прогон `pytest -m "not real_model"` не должен стать хуже baseline (т.е. число падений ≤ 9, а лучше 0).

## Out of Scope

- `make test-real` (нужен OPF) — отдельная команда перед релизом.
- Новые фичи, рефакторинг архитектуры.
- Доки/артефакты других фич.

## Tasks

### T1: mypy — api/rules.py (7× dict type-arg) — P0
- Аннотировать `dict[str, ...]` в 7 местах (строки 67, 72, 116, 126, 143, 181, 222, 251).
**Verify:** `mypy backend/neironir/api/rules.py`

### T2: mypy — privacy/feedback_analyzer.py (3) — P0
- стр. 80 ClassVar assignment; стр. 285, 374 dict type-arg.
**Verify:** `mypy backend/neironir/privacy/feedback_analyzer.py`

### T3: mypy — admin/training.py (1 dict) — P0
**Verify:** `mypy backend/neironir/admin/training.py`

### T4: mypy — auth/max_body_size.py (1 no-untyped-def) — P0
**Verify:** `mypy backend/neironir/auth/max_body_size.py`

### T5: mypy — auth/dependencies.py (3 unused ignore) — P0
**Verify:** `mypy backend/neironir/auth/dependencies.py`

### T6: mypy — admin/router.py (1 call-overload) — P0
**Verify:** `mypy backend/neironir/admin/router.py`

### T7: mypy — main.py (2 no-untyped-def/no-any-return) — P0
**Verify:** `mypy backend/neironir/main.py`

### T8: pre-existing — разбор и фиксы тестов — P1
- Каждое из 9 падений: диагностика, починка (код или тест) или задокументированное исключение.
**Verify:** `pytest -m "not real_model" -q` → падений ≤ baseline; отчёт.

### T9: полная верификация — P0
- `ruff check .`; `mypy backend/neironir`; `pytest -m "not real_model" -q`.
**Verify:** команды; результат в review.md.
