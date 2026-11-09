# Review: Code Review Fixes

> Status: Passed
> Spec: `.ai/sdd/specs/006-code-review-fixes/`
> Source: `code-review-agent-report.md` (opus + deepseek cross-review); requirements/design велись в отчёте ревью, отдельных файлов нет
> Implementation: коммит `ff325dc` (2026-11-06, ночной прогон оркестратора)
> Review closed: 2026-11-09 (задним числом — прогон спеки 008)

## Verification

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Typecheck | `uv run mypy backend/neironir` | PASS; 41 files |
| Frontend syntax | `node --check frontend/app.js && node --check frontend/admin.js` | PASS |
| Full mock regression (на момент прогона) | `uv run pytest -m "not real_model" -q` | PASS: 504 passed, 21 skipped, 0 failed (final verification ночного прогона) |
| Full mock regression (текущая) | `uv run pytest -m "not real_model" -q` | PASS: 604 passed, 0 failed, 21 skipped (после спек 007/008) |
| Real model | `make test-real` | NOT RUN в прогоне 006; на момент закрытия review — PASS 7/0 (спека 008, opf в PATH) |

## Regression Result

Все T1–T17 выполнены и зафиксированы в `tasks.md` (Execution Log,
heartbeat `docs/night-006-heartbeat.log`):

- **P0 (T1–T3):** DOCX PII-leaks закрыты — замены через границу абзаца
  дают ValueError (+счётчик reject), hyperlink-абзацы сохраняют видимый
  текст, вложенные таблицы рекурсивно обходятся и санитизируются.
- **P1 (T4–T8):** целостность feedback-петли — гейт статуса COMPLETED,
  идемпотентный повтор apply (`applied_actions.json` + `result.orig`),
  пересекающиеся ADD-диапазоны → 400 до записи, строгая позиционная
  сверка аннотаций с плейсхолдерами (мисматч → 409), строгая схема
  `FeedbackItemIn` (Literal action, EntityType, ge=0, start<=end).
- **P2 (T9–T12):** гонки фронтенда и UI — safeNext-allowlist в login.js
  (open redirect #21), токены uploadSeq/reviewSeq против stale-ответов
  (#22/#23), reviewBtn только для md, PRIVATE_ORGANIZATION,
  URL-toolbar, остановка polling (#24–#27).
- **P3 (T13–T17):** strict Origin/Referer (scheme+netloc, null → 403),
  allowlist расширений и resolved-path в save_result, тайминг-стойкая
  сверка API-ключей, эскалация terminate→kill→reap в training,
  404 result_missing на потерянный файл.

Дополнительно в прогоне исправлены два предсуществующих падения:
deadlock `_probe_subprocess()` в event loop (комбинированный режим
молча деградировал в mock на Windows) и отсутствие константы
`HTTP_413_CONTENT_TOO_LARGE` в starlette 0.38.

## Accepted Limitations

- Спека 006 — реверс-документация по код-ревью: отдельные
  requirements.md/design.md не велись, источник —
  `code-review-agent-report.md`.
- Ограничение bounded `UploadFile.read()` на уровне endpoint (T2-класс
  решений) позже закрыто на уровне ASGI receive в спеке 007 T2.
- `make test-real` на момент прогона не выполнялся (OPF-окружение);
  обязательный pre-release гейт введён релизом v0.2.0 (P-008) и зелёный
  на момент закрытия этого review.

## Documentation State

Контракты, введённые фиксами 006 (409 job_not_ready/corrupted,
400 overlapping_actions, 404 result_missing, safe-redirect), отражены в
`docs/api.md`; security-контракты дополнительно расширены спекой 007.
