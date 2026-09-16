# Code Review Report: neironir

**Дата:** 2025-07-17 (итерация 3 — верификация исправлений + новый аудит)
**Проект:** neironir — веб-сервис для удаления персональных данных из текстовых документов
**Стек:** Python 3.11+, FastAPI, Pydantic v2, privacy-filter (OPF), vanilla JS frontend
**Охват итерации 3:** проверка статуса всех находок итераций 1–2, полный аудит `privacy/`, `workers/`, `converters/`, `auth/`, `admin/`, фронтенда; эмпирическая верификация подозрительных мест на живом Python

**Сводка качества кода:** `ruff` — чисто · `mypy --strict` — чисто (40 файлов) · `pytest` — 411 passed / 8 skipped (real_model) · coverage — **86 %**.

---

## 1. Общая оценка

| Критерий | Итерация 2 | Итерация 3 | Комментарий |
|----------|-----------|-----------|-------------|
| Архитектура | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Слои, Protocol, DI — без изменений, всё чисто |
| Качество кода | ⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | Оба красных бага починены; остался 1 подтверждённый баг в feedback-loop + мёртвый код |
| Безопасность | ⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | Open redirect и валидация правил починены; CSRF-дизайн крепкий |
| Тестирование | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 411 тестов зелёные; есть слабые места покрытия (auth-deps 45 %, rules 56 %) |
| Документация | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐½ | 1 расхождение docstring↔код в `CombinedPrivacyClient` |
| Инфраструктура | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | CI, Makefile, uv, coverage-gate — без изменений |

**Итоговая оценка: 4.5/5** (на уровне итерации 2: красные баги закрыты, новых красных нет; обнаружен 1 средний баг в генераторе авто-правил и накопился мёртвый код).

---

## 1a. Итерация 4 — все замечания исправлены (2025-07-17)

По результатам итерации 3 все замечания (кроме одного осознанно отложенного) исправлены, тесты расширены и сервис перезапущен.

| ID | Статус | Что сделано |
|----|--------|-------------|
| N1 `\b`-баг | ✅ | `_wrap_word_boundaries()` ставит `\b` только у словесных символов; применён к `_extract_digit_pattern` и `_extract_generic_regex`; + 2 регресс-теста (телефон с `+` матчит, ИНН сохраняет границы) |
| N2 `domain/feedback.py` | ✅ | Модуль удалён (не было ни одного импорта) |
| N3 DOCX-ветка applier | ✅ | Мёртвая ветка удалена, `assert output_ext == "md"`, удалён неиспользуемый `_initial_placeholder` |
| N4 docstring combined | ✅ | Docstring честно описывает эвристику по длине (не «context prefix») |
| N5 организации | ✅ | Новый `EntityType.PRIVATE_ORGANIZATION` + `<PRIVATE_ORGANIZATION{n}>`; org-правила/словарь → новый тип; `_FULL_DETECTED_TYPES`; фронтенд (labels, toolbar, legend, tag-org CSS); тесты entity_type/rule_detector обновлены |
| N6 сравнение пароля | ✅ | `secrets.compare_digest` для username и password |
| 3.4 тройной `_overlaps` | ✅ | Единый `overlaps()` в `client.py`, импортируется combined/rules |
| 3.5 синглтон | ⚠️ | Оставлен (для MVP приемлемо); runtime-timeout уже решён через `update_privacy_timeout` |
| 4.3 `log_tail=[""]` | ✅ | `= []` |
| 4.5 `__import__("logging")` | ✅ | Module-level `import logging` + `logger` в docx.py |
| 4.6 `_detect_source` | ✅ | Поле `source` в `EntitySpan` (`model`/`rule`/`user`); проставляется в rules/combined; pipeline пишет `span.source`; эвристика удалена |
| 7 `_FALLBACK_RU_ID_PATTERN` | ✅ | Удалён |
| 8 `LOG_TAIL_MAX` | ✅ | Перенесён наверх, используется вместо хардкода `50` |
| 12 `header_csrf_sid` | ✅ | Переименован в `session_csrf_sid` (csrf.py, dependencies.py, тесты) |
| 13 add_manual_rule | ✅ | JSON-тело через `ManualRuleIn` (Pydantic) вместо query-params; тесты обновлены |
| 14 `escapeHtml` (app.js) | ✅ | Удалена (рендер идёт через DOM API) |
| 15 timezone | ✅ | `Job.created_at/finished_at` → `datetime.now(UTC)`; `pipeline` тоже; `stats` нормализует naive→UTC (backward compat со старыми job.json) |
| 16 `asyncio.Lock` на импорте | ⚠️ | Осознанно не тронут: в Python 3.12+ loop-binding ленивый, риск теоретический, изменение рискованнее пользы |
| 17 дубль EMAIL/PHONE | ✅ | `EMAIL_PATTERN`/`PHONE_PATTERN` — канонические в `client.py`, импортируются в `rules.py` |

**Итог итерации 4:** ruff — чисто · mypy --strict — чисто (39 файлов) · pytest — **414 passed / 8 skipped** (+3 новых регресс-теста) · coverage **87 %** (было 86 %; feedback_applier 85→90 % после удаления мёртвого кода).

Smoke-тест на живом сервисе подтвердил: «ООО «Моторинвест»» → `<PRIVATE_ORGANIZATION1>` с `source=rule`; `source` корректно проставляется; `created_at` — aware UTC; admin-stats и feedback-лист работают со старыми naive-данными; apply-feedback работает.

---

## 2. ✅ Статус находок прошлых итераций

| ID | Итерация | Тяжесть | Статус | Где проверено |
|----|----------|---------|--------|---------------|
| BUG-1 сортировка по UUID в `compute_jobs_with_feedback` | 2 | 🔴 | **✅ ИСПРАВЛЕНО** | `admin/stats.py` теперь собирает все `results`, затем `results.sort(key=lambda r: r.finished_at or r.created_at, reverse=True)` и только потом `[:limit]` |
| BUG-2 runtime-timeout не применялся до рестарта | 2 | 🔴 | **✅ ИСПРАВЛЕНО** | Добавлена `update_privacy_timeout()` — мутирует `timeout_s` живого синглтона in-place (распаковывая `CombinedPrivacyClient`); мёртвый атрибут `_runtime_timeout_override` удалён |
| 3.1 двойная токенизация `privacy_filter_cmd` | 2 | 🟡 | **✅ ИСПРАВЛЕНО** | Единая `parse_opf_cmd()` в `config.py`; `_build_subprocess_client` и `_opf_cmd` используют её |
| 3.2 open redirect в `GET /login` | 2 | 🟡 | **✅ ИСПРАВЛЕНО** | `_safe_next_url()` выделен и применяется в обоих обработчиках (проверка scheme/netloc/`//`/`\`) |
| 3.3 `entity_type` не валидировался в ручных правилах | 2 | 🟡 | **✅ ИСПРАВЛЕНО** | `EntityType(entity_type)` + `re.compile(pattern)` в `add_manual_rule` → 422 |
| 3.4 тройное дублирование `_overlaps` | 1 | 🟡 | **❌ ОТКРЫТО** | 3 копии: `client.py:166`, `combined.py:126`, `rules.py:418` (`_overlaps_rules`) |
| 3.5 глобальный синглтон `_privacy_client` | 1 | 🟡 | **⚠️ ЧАСТИЧНО** | Для timeout — решено (`update_privacy_timeout`). Синглтон по-прежнему не потокобезопасен и фиксирует режим при первом вызове |
| 3.6 дублирование DOCX pipeline/applier | 1 | 🟡 | **⚠️ ИЗМЕНИЛОСЬ** | API теперь режет DOCX apply-feedback на 400 (`docx_output_not_supported`), поэтому DOCX-ветка в `FeedbackApplier.apply()` стала мёртвым кодом (см. N6) |
| 4.1 незащищённый `json.loads(job.json)` | 2 | 🟢 | **✅ ИСПРАВЛЕНО** | `get_document_detail` обёрнут в try/except → 500-envelope |
| 4.2 мёртвый `_default_since` | 2 | 🟢 | **✅ ИСПРАВЛЕНО** | Удалён |
| 4.3 странный `log_tail=[""]; .clear()` | 2 | 🟢 | **❌ ОТКРЫТО** | `training.py::_monitor` — бессмысленная инициализация |
| 4.4 неатомарная запись `_persist_counters` | 2 | 🟢 | **✅ ИСПРАВЛЕНО** | Теперь через `atomic_write` |
| 4.5 `__import__("logging")` в функции | 2 | 🟢 | **❌ ОТКРЫТО** | `converters/docx.py:355` |
| 4.6 хрупкая эвристика `_detect_source` | 2 | 🟢 | **❌ ОТКРЫТО** | По-прежнему восстанавливает источник по тексту, а не по тегу span |

**Итог по долгу:** из 15 пунктов закрыто 9, 3 архитектурных (3.4/3.5/4.6) и 3 косметических (4.3/4.5) перенесены. Красных багов не осталось.

---

## 3. 🟡 Новый подтверждённый баг

### N1. `_extract_digit_pattern` генерирует неработающие правила для шаблонов, начинающихся с не-словесного символа

**Файл:** `backend/neironir/privacy/feedback_analyzer.py:124` (`_extract_digit_pattern`)
**Влияние:** Phase-2 цикл авто-правил. Подтверждено эмпирически.

Функция оборачивает сгенерированный паттерн в `\b … \b`. Для шаблонов, начинающихся с цифры (`\bИНН\s*\d{10}\b`), это работает. Но для шаблонов, начинающихся с `+` / `(` / кавычек, ведущий `\b` **не срабатывает** — граница слова между двумя не-словесными символами (или между началом строки и `+`) не существует.

```
phone pattern:  \b\+\d{1}\s*\(\d{3}\)\s*\d{3}\-\d{2}\-\d{2}\b
matches "+7 812 456-78-90"?  False   ← правило ничего не находит
inn pattern:    \bИНН\s*\d{10}\b
matches "ИНН 7743776572"?    True    ← работает
```

Таким образом, одобренное авто-правило для телефонов (и любых PII, начинающихся с `+`/`(`/`«`) **молча ничего не детектирует**. Правило появляется в списке как `approved`, но `RuleBasedDetector` его не применяет (нет матчей), и пользователь не получает сигнала об ошибке.

**Условие срабатывания:** 3+ ADD-корректировки телефонов одинаковой структуры (номера разные → нормализуются в один `pattern_key`) → формируется proposal → админ одобряет → правило мертво.

**Исправление:** ставить `\b` только если паттерн действительно начинается/кончается словесным символом, либо заменить на `(?<![\w+]) … (?![\w])` / `(?:^|\s)`. Минимально — отбросить ведущий `\b`, если первый токен — `\+`/`\(`.

---

## 4. 🟡 Средние проблемы

### N2. Мёртвый модуль `domain/feedback.py` (0 % coverage)

`FeedbackAction`, `FeedbackItem`, `AnnotationFeedback` **нигде не импортируются** (grep подтверждает). API использует `schemas.FeedbackSubmit`/`FeedbackItemIn` и сырые `dict`. Весь модуль — мёртвый код. Либо удалить, либо действительно перевести на него API-слой.

### N3. Мёртвая DOCX-ветка в `FeedbackApplier.apply()` + хрупкая валидация

API-эндпоинт `apply_feedback` явно режет DOCX-вывод HTTP 400 (`docx_output_not_supported`). Поэтому `else`-ветка в `FeedbackApplier.apply()` (полная пересборка DOCX из `source.docx`) **недостижима через API**. Внутри неё — хрупкая проверка:

```python
if ann.get("entity_type") and EntityType(str(ann["entity_type"])) in EntityType.__members__.values()
```

`EntityType(...)` выбрасывает `ValueError` на невалидном типе **до** `in` — list-comprehension падает. Это мёртвый код с латентным багом.

**Решение:** удалить DOCX-ветку (раз функциональность отключена) или вернуть поддержку и покрыть тестами. Заодно упростить проверку до `try: EntityType(...) except ValueError: continue`.

### N4. Несоответствие docstring ↔ код в `CombinedPrivacyClient.annotate`

Docstring обещает: *«Rule spans that **include a context prefix** (ИНН, ОГРН, …) win over model spans of different type»*. Код же проверяет **только длину**, без анализа наличия префикса:

```python
and rule_span.end - rule_span.start >= model_span.end - model_span.start
```

Любое более длинное правило иного типа побеждает модель — даже без контекстного префикса. Эвристика разумная, но описание вводит в заблуждение. Либо поправить docstring, либо реально детектить префикс.

### N5. Организации классифицируются как `EntityType.PRIVATE_PERSON`

В `privacy/rules.py` правила `_ORG_WITH_QUOTES_PATTERN` / `_ORG_WITH_BRACKETS_PATTERN` и словарные совпадения `_match_dictionaries` дают `EntityType.PRIVATE_PERSON`. В `EntityType` нет значения для организаций, поэтому «ООО «Моторинвест»» превращается в `<PRIVATE_PERSON1>`. Это:
- семантическая подмена (затрудняет восстановление/аудит),
- ломает группировку в `FeedbackAnalyzer` (статистика по `private_person` смешивает людей и компании).

**Рекомендация:** ввести `EntityType.PRIVATE_ORGANIZATION` (+ шаблон `<PRIVATE_ORGANIZATION{n}>`) либо явно документировать, что организации умышленно редуцируются к персоне.

### N6. Не-constant-time сравнение пароля

`api/auth.py::post_login` — `username != settings.admin_user or password != settings.admin_password`. Для однопользовательского localhost-MVP тяжесть низкая, но `secrets.compare_digest` стоит копейки и убирает тайинг-канал совсем.

---

## 5. 🟢 Мелкие замечания и мёртвый код

| # | Файл | Замечание |
|---|------|-----------|
| 7 | `privacy/rules.py:317` | `_FALLBACK_RU_ID_PATTERN` определён, но **нигде не используется** — мёртвый код |
| 8 | `admin/training.py:668` | `LOG_TAIL_MAX = 50` объявлен, но `_monitor` использует хардкод `50` (`if len(log_tail) > 50`) — константа не используется |
| 9 | `admin/training.py::_monitor` | `log_tail: list[str] = [""]; log_tail.clear()` — бессмысленная инициализация (можно `= []`) |
| 10 | `converters/docx.py:355` | `__import__("logging").getLogger(...)` внутри `_set_cell_text` при доступном модуле — заменить на обычный `import logging` сверху |
| 11 | `workers/pipeline.py::_detect_source` | Хрупкая эвристика источника span по тексту; лучше добавить поле `source` в `EntitySpan` и проставлять в клиентах (заявлено в TODO ещё в итерации 1) |
| 12 | `auth/dependencies.py` | Параметр `verify_csrf_token(..., header_csrf_sid=...)` назван misleadingly — туда передаётся sid **из session-cookie**, а не из заголовка. Косметика |
| 13 | `api/rules.py` | `add_manual_rule` принимает `entity_type`/`pattern` как **query-params** (а не тело) — длинные regex попадают в URL и логи. Лучше Pydantic-схема тела |
| 14 | `frontend/app.js` | `escapeHtml()` определена, но не используется (рендер идёт через DOM API). Удалить |
| 15 | `domain/job.py` + `admin/router.py` | Смешанные часовые пояса: `Job.created_at`/`finished_at` — наивные `datetime.now()`, а `_now_iso()` и `TrainingState` — `datetime.now(UTC)`. Сейчас нигде не сравниваются aware↔naive (TypeError), но хрупко — унифицировать на UTC |
| 16 | `admin/training.py` | `_STATE_LOCK = asyncio.Lock()` создаётся на импорте модуля и привязывается к первому event-loop. Под pytest-asyncio с per-test loop — латентный риск `got Future attached to a different loop` (сейчас 411 тестов проходят, но при росте тестовой базы возможны flaky-провалы) |
| 17 | `privacy/client.py` + `privacy/rules.py` | `_EMAIL_PATTERN`/`_PHONE_PATTERN` дублируются между модулями (mock-client и rule-detector). Вынести в общий модуль констант |

---

## 6. Покрытие тестами — слабые места

Coverage **86 %** общий, но есть провалы:

| Модуль | Coverage | Комментарий |
|--------|----------|-------------|
| `domain/feedback.py` | **0 %** | мёртвый модуль (N2) |
| `auth/dependencies.py` | 45 % | `get_session_payload` (fallback-ветка), `verify_csrf` (несовпадение → 403), `_csrf_sid_from_session` почти не покрыты на unit-уровне. Интеграционно входят частично |
| `api/rules.py` | 56 % | `generate_proposals`, `approve_rule`, `reject_rule`, `add_manual_rule` — часть веток не пройдена |
| `converters/docx.py` | 63 % | таблицы, очистка гиперссылок, cross-boundary clipping — недотестировано |
| `api/dependencies.py` | 78 % | `_probe_subprocess` (ветки WindowsApps), `update_privacy_timeout` (false-возврат) |

**Рекомендация:** добавить targeted unit-тесты на CSRF-reject (403) и на `add_manual_rule`/`approve_rule` happy-path — это критичные для безопасности/функциональности пути.

---

## 7. Сильные стороны (подтверждены в итерации 3)

- **Архитектура:** чистые слои `domain → privacy → converters → workers → api → auth/admin`; `Protocol` вместо ABC; DI через FastAPI с `dependency_overrides` в тестах.
- **Безопасность auth:** подписанная session-cookie (`itsdangerous.URLSafeTimedSerializer` + TTL), CSRF double-submit **с привязкой к сессии** (`csrf_sid`) и `secrets.compare_digest`, строгий CSP, `HttpOnly`+`SameSite=Lax`, `Secure` под HTTPS, проверка `Origin`/`Referer` на POST-логине, валидация `next=`.
- **Надёжность I/O:** `atomic_write` (temp+`os.replace`) во **всех** точках персистентности (включая счётчики и runtime-настройки); fallback subprocess→mock; обработка CP1251 в выводе OPF; буферизация датасета в памяти + атомарный flush.
- **Регрессия:** ruff + mypy `--strict` (40 файлов) — 0 замечаний; 411 тестов зелёные; маркер `real_model` для тяжёлых интеграций.
- **Frontend:** рендер подсветки через DOM API (без `innerHTML`-инъекций), корректная работа с CSRF из JS-клиента, аккуратное позиционирование тулбара выделения.

---

## 8. Реестр находок (итог итерации 3)

| ID | Тяжесть | Тип | Описание |
|----|---------|-----|----------|
| N1 | 🟡 | **баг (подтв.)** | `\b`-баг: авто-правила для PII, начинающихся с `+`/`(`/`«`, молча не детектят |
| N2 | 🟡 | мёртвый код | `domain/feedback.py` — весь модуль не используется (0 % cov) |
| N3 | 🟡 | мёртвый+баг | DOCX-ветка `FeedbackApplier` недостижима + хрупкая `EntityType in __members__.values()` |
| N4 | 🟡 | doc↔code | `CombinedPrivacyClient`: docstring про «context prefix», код — про длину |
| N5 | 🟡 | домен | Организации редуцируются к `PRIVATE_PERSON` — семантическая подмена |
| N6 | 🟡 | безопасность (низко) | Не-constant-time сравнение пароля |
| 3.4 | 🟡 | долг | Тройное дублирование `_overlaps` |
| 4.3/4.5/4.6 | 🟢 | долг | Косметика/хрупкость (перенесено с прошлых итераций) |
| 7–17 | 🟢 | разные | Мёртвый код (`_FALLBACK_RU_ID_PATTERN`, `LOG_TAIL_MAX`, `escapeHtml`), стиль, смешанные TZ, asyncio.Lock на импорте |

---

## 9. Заключение

Ядро пайплайна (upload → annotate → result) и весь auth/admin-контур **продакшн-готовы в рамках MVP**. Оба красных бага из итерации 2 закрыты и проверены; новых красных нет. Кодовая база проходит ruff, mypy `--strict` и 411 тестов при 86 % покрытия.

Из нового — один подтверждённый баг средней тяжести (N1: авто-правила feedback-loop для телефонов молча неработоспособны из-за `\b`-границы) и накопившийся мёртвый код (`domain/feedback.py`, `_FALLBACK_RU_ID_PATTERN`, `LOG_TAIL_MAX`, DOCX-ветка applier, `escapeHtml`). Ни одно из них не блокирует релиз; N1 стоит починить до того, как цикл авто-правил начнёт активно использоваться админами.

**Приоритет работ:**
1. **N1** — фикс `\b` в `_extract_digit_pattern` (+ регрессионный тест).
2. **N2, N3, п.7–8, п.14** — зачистка мёртвого кода (один PR, ~30 минут).
3. **N5** — решение по организацийному типу (продуктовое).
4. Покрытие `auth/dependencies.py` (CSRF-reject) и `api/rules.py` (approve/reject).
5. Долг по 3.4 / 4.3 / 4.5 / 4.6 — когда дойдут руки.
