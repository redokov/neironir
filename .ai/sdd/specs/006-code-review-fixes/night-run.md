# Ночной прогон: спека 006 Code Review Fixes

## Шаг 1 — взвод (в night-сессии, ДО старта задачи)

```bash
python C:/Tools/pi-billing-window/scripts/arm_cont_after_reset.py \
  "$PI_SESSION_FILE" --repeat 8 --ttl 20
```

- repeat 8 = до 8 срабатываний на границах 2-часовых окон (≈16 ч покрытия)
- Проверка: `arm_cont_after_reset.py "$PI_SESSION_FILE" --check`

## Шаг 2 — промт (вставить в night-сессию целиком)

---

Работай автономно всю ночь. Исправь дефекты из код-ревью по плану спеки 006.

**Источник истины:** `.ai/sdd/specs/006-code-review-fixes/tasks.md` — план одобрен, задачи T1–T17 в приоритетном порядке (P0→P3). Контекст находок: `code-review-agent-report.md`. Отчёт по каждой находке: файл, строка, описание, fix-подсказка.

**Порядок работы (оркестратор, паттерн `workers/pipeline.run_job()`):**
Механика ночного прогона вынесена в `scripts/night_orchestrator.py` — линейный пайплайн задач, каждый шаг: верификация → запись Execution Log + heartbeat → переход к следующей.

```bash
uv run python scripts/night_orchestrator.py status   # текущее состояние Execution Log + heartbeat
uv run python scripts/night_orchestrator.py next     # следующая незавершённая задача
uv run python scripts/night_orchestrator.py verify   # ruff + mypy + pytest после задачи
uv run python scripts/night_orchestrator.py setdone  <T<N>> "<evidence-вывод>"
uv run python scripts/night_orchestrator.py setblocked <T<N>> "<причина>"
```

**Человеческий агент (worker):**
1. Запроси `night_orchestrator next` → получи T1–T17.
2. Реализуй задачу строго по её чек-листу. Правь только файлы из задачи, никакого рефакторинга за пределами скоупа.
3. Для каждой задачи добавь regress-тест (файл теста указан в задаче).
4. Верификация после КАЖДОЙ задачи, все три команды обязаны быть зелёными:
   - `uv run ruff check .`
   - `uv run mypy backend/neironir`
   - `uv run pytest -m "not real_model"`
5. Зафиксируй прогресс: дополни Execution Log в tasks.md (задача, статус, evidence-вывод верификации) И допиши строку в heartbeat: `docs/night-006-heartbeat.log` (формат: `YYYY-MM-DD HH:MM T<N> done|blocked`).
6. Переходи к следующей задаче.

**Правила выживания:**
- Всякий прогресс — сразу на диск (Execution Log + heartbeat), не держи состояние в чате.
- Если придёт сообщение «продолжи» — продолжай с места остановки: восстанови контекст из Execution Log и heartbeat, это норма ночного режима.
- Одна задача — не более 2 попыток прохождения верификации. Если 2 раза упало — пометь задачу `blocked` в Execution Log с причиной, иди к следующей. Возвращаться к blocked в эту ночь не нужно.
- Задачи D1–D3 (deferred) НЕ делать. Находки #14, #15 (training.py:279, training.py:191) опровергнуты кросс-ревью — НЕ «исправлять» их.
- Backend — обычный pytest-прогон, GUI не нужен. Ничего разрушительного (no clean, no storage_prod, no git push).

**Завершение:** когда все T1–T17 имеют статус Done или blocked:
1. Финальная верификация: `make check` эквивалент (ruff + mypy + pytest целиком).
2. Напиши итог в чат: сколько Done / blocked, список blocked с причинами, суммарный diff-стат по файлам (git status).
3. Сними флаг: `python C:/Tools/pi-billing-window/scripts/arm_cont_after_reset.py "$PI_SESSION_FILE" --remove`.
4. Остановись. Новых задач не брать.

---

## Утром — проверка (из любой сессии)

```bash
grep -E "fire:reset-ready|fire:send-ok|fire:confirmed|send-error|capitulation|arm-gone" \
  ~/.pi/agent/pi-billing-window-arms.log | tail -30
tail -20 c:/MyProjects/neurodoc/docs/night-006-heartbeat.log
grep -cE "Done" c:/MyProjects/neurodoc/.ai/sdd/specs/006-code-review-fixes/tasks.md
```

`fire:send-ok → fire:confirmed` после каждой границы окна + растущий heartbeat = ночь отработала.
`arm-gone` раньше конца ночи = флаг сгорел, автопродолжения не будет — blocker.
