# Фаза 4. Frontend (минимум)

## Цель

Минимальный SPA: одна страница, drop-zone, прогресс, кнопки скачивания. Без сборщиков, без фреймворков — чистый HTML/JS/CSS. Без аутентификации (её нет в архитектуре).

## Входные условия

- Фаза 3 завершена: backend отвечает на `/api/v1/documents`.
- `uvicorn` запускается, `GET /` отдаёт `frontend/index.html`.

## Что делаем

### 1. Файлы

- `frontend/index.html`
- `frontend/app.js`
- `frontend/styles.css`

### 2. `index.html` — структура

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <title>neironir</title>
    <link rel="stylesheet" href="/static/styles.css">
  </head>
  <body>
    <main class="container">
      <h1>neironir</h1>
      <p class="lead">Удаление персональных данных из документов перед отправкой в нейросеть.</p>

      <section id="upload-section" class="card">
        <div id="dropzone" class="dropzone" tabindex="0">
          <p>Перетащите файл сюда или <button type="button" id="pick" class="link-button">выберите</button></p>
          <p class="hint">.md или .docx, до 20 МБ</p>
          <input id="file-input" type="file" accept=".md,.docx" hidden>
        </div>
      </section>

      <section id="job-section" class="card" hidden>
        <h2>Задача</h2>
        <dl class="job-info">
          <dt>Имя файла</dt><dd id="job-filename">—</dd>
          <dt>Статус</dt><dd id="job-status">—</dd>
        </dl>
        <div class="actions">
          <a id="download" class="button primary" href="#" download hidden>Скачать очищенный файл</a>
        </div>
        <p id="error" class="error" hidden></p>
      </section>
    </main>
    <script src="/static/app.js" defer></script>
  </body>
</html>
```

### 3. `app.js` — логика

- `const POLL_INTERVAL_MS = 1500;`
- Состояние: `currentJobId`, `pollTimer`.
- Элементы: `dropzone`, `fileInput`, `pick`, `jobSection`, `jobFilename`, `jobStatus`, `download`, `error`.
- Обработчики:
  - `click` на `#pick` → `fileInput.click()`.
  - `dragover`/`dragenter` на `dropzone` → `preventDefault()`, подсветка.
  - `dragleave`/`drop` → снять подсветку.
  - `drop` → `file = e.dataTransfer.files[0]`, вызвать `upload(file)`.
  - `change` на `fileInput` → `file = e.target.files[0]`, вызвать `upload(file)`.
  - клик-клик по `dropzone` (на фокусе/Enter/Space) → `fileInput.click()` (a11y).
- `async function upload(file)`:
  - проверка расширения: `/\.(md|docx)$/i.test(file.name)`. Иначе показать ошибку.
  - проверка размера: `file.size <= MAX_FILE_SIZE` (20 МБ, константа в JS). Иначе ошибка.
  - `const form = new FormData(); form.append("file", file);`
  - `const res = await fetch("/api/v1/documents", { method: "POST", body: form });`
  - `if (!res.ok) → показать error с сообщением из ответа (поле error или message)`.
  - `const job = await res.json();`
  - `currentJobId = job.id;`
  - `jobSection.hidden = false; jobFilename.textContent = job.source_filename; jobStatus.textContent = job.status; download.hidden = true;`
  - `startPolling();`
- `function startPolling()`:
  - `pollTimer = setInterval(poll, POLL_INTERVAL_MS);`
- `async function poll()`:
  - `const res = await fetch("/api/v1/documents/" + currentJobId);`
  - `if (!res.ok) → stopPolling(), показать error`.
  - `const job = await res.json();`
  - `jobStatus.textContent = job.status;`
  - если `job.status === "completed"`:
    - `stopPolling();`
    - `download.href = "/api/v1/documents/" + currentJobId + "/download";`
    - `download.hidden = false;`
  - если `job.status === "failed"`:
    - `stopPolling();`
    - `error.hidden = false; error.textContent = job.error || "Ошибка обработки";`
- `function stopPolling()`:
  - `if (pollTimer) clearInterval(pollTimer); pollTimer = null;`
- При уходе со страницы (`beforeunload`) — `stopPolling()` (не критично, но аккуратно).

### 4. `styles.css` — стили

- Без внешних фреймворков.
- Шрифт: системный (`-apple-system, "Segoe UI", Roboto, sans-serif`).
- Цвета: нейтральные. Один акцент для primary-кнопки.
- `.dropzone` — большая рамка, dashed, фокус и drag-over меняют фон.
- `.card` — белый фон, тень, скругления, отступ.
- На мобильном: `.container` — `max-width: 720px; margin: 0 auto; padding: 16px;`, всё одной колонкой.

### 5. Тесты

UI-тесты на этом этапе **не пишем** (нужен был бы Playwright/Cypress, что выходит за рамки минимума). Ручная проверка — обязательна.

Что проверить вручную (записать в `docs/agents/04-frontend.md` чек-листом, не автоматизировать):

- [ ] Открыть `http://127.0.0.1:8000/`, видна форма.
- [ ] Перетащить `.md` — задача появляется, статус переходит в `completed`, появляется ссылка «Скачать».
- [ ] Скачанный файл содержит плейсхолдеры, формат `.md`.
- [ ] То же с `.docx` — формат сохранился, текст изменён.
- [ ] Загрузить `.txt` — появляется сообщение об ошибке.
- [ ] Загрузить файл > 20 МБ — сообщение об ошибке.
- [ ] В DevTools видно, что polling идёт и прекращается после `completed`.

## Критерии приёмки

- [ ] `GET /` отдаёт `index.html` со всеми секциями.
- [ ] `/static/styles.css` и `/static/app.js` доступны.
- [ ] Ручной чек-лист пройден полностью.
- [ ] `uv run ruff check .` и `uv run ruff format --check .` — без ошибок (JS не линтуем).
- [ ] `uv run pytest` — тесты backend из фазы 3 по-прежнему зелёные.

## Вне scope

- Сборщики (Vite, webpack).
- React/Vue/любой фреймворк.
- Авторизация.
- Drag-and-drop нескольких файлов (только один).
- Превью «до/после» в браузере.
- Прогресс-бар в процентах (у нас нет эндпоинта прогресса — только статусы).
- Локализация на другие языки.
