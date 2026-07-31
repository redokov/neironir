# Tasks: Stabilize Phase 2

> Requirements: @requirements.md
> Design: @design.md
> Status: Approved

## Execution Log (2025-01-15)

| Task | Status | Evidence |
|------|--------|----------|
| T1 rules.py | Done | `mypy backend/neironir/api/rules.py` — 0 |
| T2 feedback_analyzer | Done | ClassVar→dict, dict args, samples cast — 0 |
| T3 training | Done | dict[str,object] + isinstance guard — 0 |
| T4 max_body_size | Done | return annotation + Response import — 0 |
| T5 auth/dependencies | Done | unused ignores удалены — 0 |
| T6 admin/router | Done | int() call-overload — isinstance guard — 0 |
| T7 main.py | Done | call_next annotation + import — 0 |
| T8 pre-existing | Done | см. ниже: 9/9 закрыто |
| T9 полная верификация | Done | ruff 0, format 0, mypy 0, pytest 357 passed |

## T8: закрытие 9 pre-existing падений

1. `test_admin_html_served` (redirect loop) — **фикс middleware**: читает settings из `request.app.state.settings` (а не из конструктора) — устранён десинк secret между middleware и login. Фикстура `client_and_storage` выставляет `app.state.settings`.
2. `test_whoami_authenticated` — тот же корень (middleware env-secret vs TEST_SECRET) — починен тем же фиксом.
3. `test_get_settings_returns_default` — устарел до введения auth; тест обновлён (login → GET).
4. `test_checkbox_*` (3), `test_banner_*` (2), `test_toolbar_*` (1) — требуют playwright (не установлен); skip-условие расширено проверкой `importlib.util.find_spec("playwright")`.
5. Бонус: `test_invalid_json_output` — обёрнут `json.JSONDecodeError` в `PrivacyFilterError` (контракт).

## T1: mypy — api/rules.py (7× dict) — P0 · 20m
- [ ] Аннотировать `dict[...]` в 7 местах.
- Files: `backend/neironir/api/rules.py`
- Verify: `mypy backend/neironir/api/rules.py` → 0

## T2: mypy — privacy/feedback_analyzer.py (3) — P0 · 20m
- [ ] ClassVar:80; dict:285, 374.
- Files: `backend/neironir/privacy/feedback_analyzer.py`
- Verify: `mypy` → 0

## T3: mypy — admin/training.py (1) — P0 · 10m
- Files: `backend/neironir/admin/training.py`
- Verify: `mypy` → 0

## T4: mypy — auth/max_body_size.py (1) — P0 · 5m
- Files: `backend/neironir/auth/max_body_size.py`
- Verify: `mypy` → 0

## T5: mypy — auth/dependencies.py (3 unused ignore) — P0 · 5m
- Files: `backend/neironir/auth/dependencies.py`
- Verify: `mypy` → 0

## T6: mypy — admin/router.py (1 call-overload) — P0 · 15m
- Files: `backend/neironir/admin/router.py`
- Verify: `mypy` → 0

## T7: mypy — main.py (2) — P0 · 10m
- Files: `backend/neironir/main.py`
- Verify: `mypy` → 0

## T8: pre-existing failures (9) — P1 · 2-4h
- [ ] Диагностика 1–6 (e2e стат-тесты) — прочитать, починить код/тест или skip с обоснованием.
- [ ] Диагностика 7 (settings).
- [ ] Диагностика 8–9 (admin redirect loop + whoami) — middleware.
- Verify: `pytest -m "not real_model" -q` — падений ≤ baseline; лучше 0.

## T9: Полная верификация — P0 · 15m
- [ ] `ruff check .` → 0
- [ ] `mypy backend/neironir` → 0
- [ ] `pytest -m "not real_model" -q` → отчёт
- Files: review.md
