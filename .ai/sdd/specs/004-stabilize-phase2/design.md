# Design: Stabilize Phase 2

> Requirements: @requirements.md
> Status: Approved

## 1. Summary

Механические mypy-фиксы (аннотации, ignore-комментарии, возвратные типы) + диагностика 9 pre-existing падений тестов. Без архитектурных изменений.

## 2. Requirements Mapping

| Requirement | Coverage |
|-------------|----------|
| FR-001 | T1–T7 (mypy) |
| FR-002 | T8 |
| FR-003 | T9 |

## 3. Technical Approach

### 3.1 mypy-фиксы (T1–T7)

Ожидаемые правки:
- `dict` → `dict[str, object]` / `dict[str, ...]` по контексту (rules.py, feedback_analyzer, training).
- ClassVar: перенести присваивание в class body (feedback_analyzer:80) или добавить `ClassVar[...]`.
- unused `type: ignore` — удалить комментарии (dependencies.py:73,77,78).
- `max_body_size.py:37` — добавить `-> None`.
- `admin/router.py:272` — `int(...)` от `object`: добавить явный `int(...)` после проверки или `cast`.
- `main.py:82` — аннотация параметров; `main.py:98` — `return Response(...)` явно или `cast`.

### 3.2 pre-existing failures (T8)

9 падений:
1. `test_checkbox_is_checked_by_default_for_docx` (e2e, стат-анализ HTML?)
2. `test_checkbox_stays_checked_after_upload`
3. `test_checkbox_keeps_user_choice_across_uploads`
4. `test_banner_explains_mock_limitations`
5. `test_real_pipeline_redacts_email`
6. `test_toolbar_is_in_viewport_after_selecting_text`
7. `test_get_settings_returns_default`
8. `test_admin_html_served` (redirect loop)
9. `test_whoami_authenticated`

Диагностика каждого: прочитать тест, понять причину, решить: (a) фикс кода, (b) фикс теста, (c) обоснованный skip. Особое внимание — 8/9 (admin middleware redirect loop) и 9 (whoami).

### 3.3 Верификация (T9)

`make check`-эквивалент: ruff + mypy + pytest (mock). Отчёт в review.md.

## 4. Risks

| Risk | Mitigation |
|------|------------|
| Фикс теста маскирует баг | Каждый фикс теста — с объяснением причины; предпочтение фиксу кода |
| mypy-строгость ломает runtime | Только аннотации, без change логики |
| Redirect loop глубже | Диагностировать middleware; если серьёзно — отдельный скоуп |

## 5. Verification Strategy

- `ruff check .`
- `mypy backend/neironir` → 0
- `pytest -m "not real_model" -q` → сравнить с baseline
