// neironir frontend SPA: drop-zone, upload, polling, download.

(function () {
  "use strict";

  var POLL_INTERVAL_MS = 1500;
  var MAX_FILE_SIZE = 20 * 1024 * 1024;

  // DOM lookups happen inside init() to keep this file easy to scan.

  var dropzone = null;
  var fileInput = null;
  var pick = null;
  var jobSection = null;
  var jobFilename = null;
  var jobStatus = null;
  var download = null;
  var errorEl = null;
  var resetBtn = null;

  var currentJobId = null;
  var pollTimer = null;

  function init() {
    dropzone = document.getElementById("dropzone");
    fileInput = document.getElementById("file-input");
    pick = document.getElementById("pick");
    jobSection = document.getElementById("job-section");
    jobFilename = document.getElementById("job-filename");
    jobStatus = document.getElementById("job-status");
    download = document.getElementById("download");
    errorEl = document.getElementById("error");
    resetBtn = document.getElementById("reset");

    pick.addEventListener("click", function (e) {
      e.stopPropagation();
      fileInput.click();
    });

    dropzone.addEventListener("click", function () {
      fileInput.click();
    });

    dropzone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.click();
      }
    });

    dropzone.addEventListener("dragover", function (e) {
      e.preventDefault();
      dropzone.classList.add("dropzone--active");
    });
    dropzone.addEventListener("dragenter", function (e) {
      e.preventDefault();
      dropzone.classList.add("dropzone--active");
    });
    dropzone.addEventListener("dragleave", function () {
      dropzone.classList.remove("dropzone--active");
    });
    dropzone.addEventListener("drop", function (e) {
      e.preventDefault();
      dropzone.classList.remove("dropzone--active");
      var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) {
        upload(file);
      }
    });

    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      // Reset so the same file can be re-selected after "Загрузить ещё".
      fileInput.value = "";
      if (file) {
        upload(file);
      }
    });

    resetBtn.addEventListener("click", reset);

    window.addEventListener("beforeunload", stopPolling);
  }

  function validateFile(file) {
    if (!/\.(md|docx)$/i.test(file.name)) {
      return "Поддерживаются только файлы .md и .docx";
    }
    if (file.size > MAX_FILE_SIZE) {
      return "Файл больше 20 МБ";
    }
    return null;
  }

  function showError(message) {
    errorEl.hidden = false;
    errorEl.textContent = message;
    jobStatus.textContent = "Ошибка";
  }

  function statusLabel(status) {
    switch (status) {
      case "pending":
        return "В очереди";
      case "processing":
        return "Обрабатывается";
      case "completed":
        return "Готово";
      case "failed":
        return "Ошибка";
      default:
        return status;
    }
  }

  function showJobSection(job) {
    jobSection.hidden = false;
    jobFilename.textContent = job.source_filename;
    jobStatus.textContent = statusLabel(job.status);
    download.hidden = true;
    errorEl.hidden = true;
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function extractErrorDetail(body) {
    // FastAPI's HTTPException produces {"detail": "..."}; some handlers
    // return {"detail": {"code": ..., "message": ...}}.
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && "message" in detail) {
        return detail.message;
      }
      if (typeof detail === "string") {
        return detail;
      }
      if (typeof body.error === "string") {
        return body.error;
      }
    }
    return null;
  }

  async function upload(file) {
    var err = validateFile(file);
    if (err) {
      showError(err);
      return;
    }

    // Show the job section immediately so the user sees progress.
    jobSection.hidden = false;
    jobFilename.textContent = file.name;
    jobStatus.textContent = "Загружается…";
    download.hidden = true;
    errorEl.hidden = true;

    var form = new FormData();
    form.append("file", file);

    var res;
    try {
      res = await fetch("/api/v1/documents", { method: "POST", body: form });
    } catch (e) {
      showError("Не удалось связаться с сервером");
      return;
    }

    if (!res.ok) {
      var message = "Ошибка загрузки (" + res.status + ")";
      if (res.status === 413) {
        message = "Файл больше 20 МБ";
      } else if (res.status === 400) {
        try {
          var body = await res.json();
          var detail = extractErrorDetail(body);
          if (detail) {
            message = detail;
          } else {
            message = "Неподдерживаемый формат файла";
          }
        } catch (_) {
          message = "Неподдерживаемый формат файла";
        }
      } else {
        try {
          var body2 = await res.json();
          var detail2 = extractErrorDetail(body2);
          if (detail2) {
            message = detail2;
          }
        } catch (_) {
          // keep generic message
        }
      }
      showError(message);
      return;
    }

    var job = await res.json();
    currentJobId = job.id;
    showJobSection(job);
    startPolling();
  }

  async function poll() {
    if (!currentJobId) {
      stopPolling();
      return;
    }
    var res;
    try {
      res = await fetch("/api/v1/documents/" + currentJobId);
    } catch (e) {
      stopPolling();
      showError("Не удалось получить статус задачи");
      return;
    }

    if (!res.ok) {
      stopPolling();
      showError("Не удалось получить статус задачи");
      return;
    }

    var job = await res.json();
    jobStatus.textContent = statusLabel(job.status);

    if (job.status === "completed") {
      stopPolling();
      download.href = "/api/v1/documents/" + currentJobId + "/download";
      download.hidden = false;
      jobStatus.textContent = "Готово";
    } else if (job.status === "failed") {
      stopPolling();
      showError(job.error || "Обработка завершилась с ошибкой");
    }
  }

  function reset() {
    stopPolling();
    currentJobId = null;
    jobSection.hidden = true;
    jobFilename.textContent = "—";
    jobStatus.textContent = "—";
    download.hidden = true;
    download.removeAttribute("href");
    errorEl.hidden = true;
    fileInput.value = "";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
