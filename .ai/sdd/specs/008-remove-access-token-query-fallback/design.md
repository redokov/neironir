# Design: Remove `?access_token=` query fallback

> Requirements: @requirements.md
> Status: Approved
> Last Updated: 2026-11-09

## 1. Executive Summary

Изменение точечное и не меняет архитектуру. На сервере из
`verify_job_access` удаляется ветка чтения `access_token` из
query-параметров — остаётся только заголовок `X-Job-Token` (плюс явный
admin bypass). Во фронтенде элемент «Скачать очищенный файл» перестаёт
быть прямой навигацией: клик перехватывается, файл запрашивается через
`fetch()` с заголовком и сохраняется через `Blob` +
`URL.createObjectURL`. Токен уже хранится в состоянии фронтенда после
spec 007 T3 (`currentJobToken`), повторной выдачи не требуется.

## 2. Requirements Mapping

| Requirement | Design Coverage | Notes |
|-------------|-----------------|-------|
| FR-001 | §3 | удаление query-ветки в `verify_job_access` |
| FR-002 | §4 | fetch + Blob + `createObjectURL` в `app.js` |
| FR-003 | §4 | переиспользование `currentJobToken` / `jobTokenHeaders()` |
| NFR-001 | §3 | все пути отказа дают один и тот же 404 `job_not_found` |

## 3. Server: `backend/neironir/api/jobs.py`

Функция `verify_job_access` (spec 007 FR-004):

- удалить ветку `request.query_params.get("access_token")`:
  `provided = request.headers.get("x-job-token")` — единственный источник
  токена (кроме admin-сессии);
- обновить docstring: убрать упоминание query-параметра, зафиксировать
  «header-only» контракт;
- убрать ставший неверным комментарий про `<a>`-навигацию.

Поведение отказа не меняется: отсутствует/неверен/legacy без digest —
один и тот же `404 job_not_found` (design §8 спеки 007).

## 4. Frontend: `frontend/app.js`

Скачивание в ветке `job.status === "completed"`:

- ссылка `$.download` больше не получает `href` с токеном: на completed
  проставляется `data-ext` и снимается `hidden` (кнопка-ссылка);
- вешается обработчик `click` c `preventDefault`: `fetch("/download", {
  headers: jobTokenHeaders() })` → при `!res.ok` показать ошибку; иначе
  `res.blob()` → `URL.createObjectURL(blob)` → временный `<a download>`
  с именем из `Content-Disposition` (или fallback) → программный клик →
  `URL.revokeObjectURL`;
- имя файла: парсить `Content-Disposition` (сервер его отдаёт), при
  неудаче — базовое имя из имени источника/`output_ext`;
- токен — из `currentJobToken` через существующий хелпер
  `jobTokenHeaders()` (FR-003), никакой передачи токена в URL;
- `admin.js` не трогается: admin-интерфейс не скачивает job-файлы по
  job-токену (проверено grep — только admin API с cookie-сессией).

## 5. Tests

- `tests/integration/test_api.py`:
  `test_download_accepts_access_token_query_param_for_link_navigation`
  (позитивный, query-параметр) — заменить на негативный
  «query-параметр не работает → 404» при валидном токене в URL;
  позитивное скачивание через заголовок уже покрыто соседними тестами.
- e2e-тесты (`tests/e2e/`) уже работают через `X-Job-Token` — не меняются.
- Новый статический regression-тест в `tests/unit/frontend/`
  (паттерн спеки 006 T10–T12): в `app.js` нет `access_token` в
  конструировании URL, есть `createObjectURL`, клик по download
  предотвращает default navigation.

## 6. Documentation

- `docs/api.md`, раздел «Job access token»: абзац про query-параметр
  заменить описанием скачивания через сценарий (fetch + заголовок).
- `CHANGELOG.md`: создать раздел `[Unreleased]` с пометкой
  «Изменено (ломающее)».
- README/architecture: упоминают только выдачу токена в ответе на
  загрузку — проверить, что query-фолбэк там не описан (по grep — не
  описан, правка не требуется).

## 7. Breaking Change and Risks

- Старые открытые ссылки `?access_token=...` → 404. Приемлемо pre-1.0.
- Риск: обработчик клика не спасует от «открыть в новой вкладке» /
  copy-link-address — но href больше не содержит токена, ссылка без
  токена бесполезна и безопасна (404).
- Риск: `URL.createObjectURL` без `revokeObjectURL` течёт память —
  revoke сразу после программного клика.

## 8. Open Questions

Нет: направление предодобрено пользователем, опция download-ticket из
идеи 005 отклонена (fetch-схема достаточна).
