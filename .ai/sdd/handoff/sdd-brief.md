# SDD Handoff Brief: neironir — состояние на 2026-11-09

> Status: active
> Readiness: все спеки 001–008 закрыты (review:done); открытых работ нет
> Updated: 2026-11-09 (прогон спеки 008; предыдущий brief — эпоха спеки 006 —
> superseded, см. историю в git)

## Project State

- **Релиз v0.2.0** выпущен и запушен (тег `v0.2.0`, HEAD на момент релиза
  `4166346`). После релиза закрыты: пробелы `docs/api.md` по контрактам 007
  (`6cad4db`), спека 008 + её ломающее изменение (`dd31a6a`), ретроспективный
  review спеки 006 (`ca82129`).
- **Спеки 001–008 — все review:done.** 001–005 (реверс-документация),
  006 (code-review fixes, T1–T17, `ff325dc`), 007 (security/privacy
  hardening), 008 (удаление `?access_token=` query-фолбэка, идея 005).

## Current Contracts (что считать источником истины)

- **Job-токен — header-only** (спека 008): `X-Job-Token` — единственный
  способ передать токен задачи; `?access_token=` в URL не авторизует
  (неразличимый 404). Скачивание из UI — `fetch` + `Blob`/
  `URL.createObjectURL`. Ломающее изменение зафиксировано в
  CHANGELOG `[Unreleased]`.
- **Граница авторизации** (спека 007): анонимная загрузка только с
  loopback; non-loopback — `Authorization: Bearer`; невалидный Bearer
  даёт 401 с приоритетом над session-cookie.
- **Digest-only токены**: в `job.json` — только SHA-256 digest; токен
  выдаётся один раз в ответе 202.

## Ideas

- **006-persistent-jti-revocation** — единственная открытая идея
  (персистентный реестр отозванных jti; сейчас реестр in-memory на
  процесс). Перед взятием в спеку — обсудить с пользователем.
- 001–005 — idea:captured/adopted, реализованы соответствующими спеками.

## Deferred Limitations (осознанно отложенные)

- Приёмный предел составной (chunked) загрузки — endpoint-level backstop
  (`UploadFile.read(max+1)`); receive-level cap для multipart отсутствует
  (ограничение публичного API Starlette).
- Максимальная длина dynamic regex — константа в коде (200), не настройка.
- Реестр отзывов jti — in-memory, на процесс (перезапуск затирает).

## Verification (показания на 2026-11-09)

```text
uv run ruff check .            → 0
uv run mypy backend/neironir   → 0 (41 files)
uv run pytest -m "not real_model" -q → 604 passed / 0 failed / 21 skipped
make test-real (opf из .venv-opf/Scripts в PATH) → 7 passed / 0 failed
node --check frontend/app.js && node --check frontend/admin.js → OK
```

## Tooling Notes

- Оркестратор `scripts/night_orchestrator.py` параметризован:
  `--spec <NNN-dirname> --heartbeat <path>` (умолчания — 006/night-006).
- opf-бинарь: `.venv-opf/Scripts/opf.exe` (на PATH его нет — добавлять
  при запуске real-гейтов). `make` в bash-окружении недоступен —
  эквивалент: `NEIRONIR_RUN_REAL_MODEL_TESTS=1 uv run pytest -m real_model`.

## Recommended Next Action

- Обсудить с пользователем идею 006 (персистентный jti-реестр) —
  единственный кандидат на спеку 009.
- Следующий релиз (0.3.0): включить ломающее изменение 008 из
  `[Unreleased]`.
