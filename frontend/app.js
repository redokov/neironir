// neironir frontend SPA: upload, poll, review, feedback.

(function () {
  "use strict";

  var POLL_INTERVAL_MS = 1500;
  var MAX_FILE_SIZE = 20 * 1024 * 1024;

  // Entity type → human label & CSS class
  var ENTITY_LABELS = {
    private_person: "ФИО",
    private_address: "Адрес",
    private_email: "Email",
    private_phone: "Телефон",
    private_date: "Дата",
    private_url: "URL",
    account_number: "Счёт/ИНН",
    secret: "Секрет",
  };

  // DOM refs (populated in init)
  var $ = {};
  var currentJobId = null;
  var pollTimer = null;
  var reviewData = null; // { text, spans }
  var pendingActions = []; // user edits not yet submitted

  function init() {
    $.dropzone = document.getElementById("dropzone");
    $.fileInput = document.getElementById("file-input");
    $.pick = document.getElementById("pick");
    $.jobSection = document.getElementById("job-section");
    $.jobFilename = document.getElementById("job-filename");
    $.jobStatus = document.getElementById("job-status");
    $.download = document.getElementById("download");
    $.errorEl = document.getElementById("error");
    $.resetBtn = document.getElementById("reset");
    $.reviewBtn = document.getElementById("review-btn");
    $.reviewSection = document.getElementById("review-section");
    $.preview = document.getElementById("preview");
    $.confirmAll = document.getElementById("confirm-all");
    $.submitFeedback = document.getElementById("submit-feedback");
    $.applyFeedback = document.getElementById("apply-feedback");
    $.skipReview = document.getElementById("skip-review");
    $.commentSection = document.getElementById("comment-section");
    $.feedbackComment = document.getElementById("feedback-comment");
    $.feedbackSuccess = document.getElementById("feedback-success");
    $.applySuccess = document.getElementById("apply-success");
    $.applyError = document.getElementById("apply-error");
    $.uploadOptions = document.getElementById("upload-options");
    $.outputFormatMd = document.getElementById("output-format-md");
    $.outputFormatHint = document.getElementById("output-format-hint");
    $.modeInfoSection = document.getElementById("mode-info-section");
    $.modeInfoText = document.getElementById("mode-info-text");

    // Upload handlers
    $.pick.addEventListener("click", function (e) {
      e.stopPropagation();
      $.fileInput.click();
    });
    $.dropzone.addEventListener("click", function () {
      $.fileInput.click();
    });
    $.dropzone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        $.fileInput.click();
      }
    });
    $.dropzone.addEventListener("dragover", function (e) {
      e.preventDefault();
      $.dropzone.classList.add("dropzone--active");
    });
    $.dropzone.addEventListener("dragenter", function (e) {
      e.preventDefault();
      $.dropzone.classList.add("dropzone--active");
    });
    $.dropzone.addEventListener("dragleave", function () {
      $.dropzone.classList.remove("dropzone--active");
    });
    $.dropzone.addEventListener("drop", function (e) {
      e.preventDefault();
      $.dropzone.classList.remove("dropzone--active");
      var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) upload(file);
    });
    $.fileInput.addEventListener("change", function () {
      var file = $.fileInput.files && $.fileInput.files[0];
      $.fileInput.value = "";
      if (file) upload(file);
    });

    // Job section handlers
    $.resetBtn.addEventListener("click", reset);
    $.reviewBtn.addEventListener("click", openReview);
    $.confirmAll.addEventListener("click", confirmAll);
    $.submitFeedback.addEventListener("click", submitFeedback);
    $.applyFeedback.addEventListener("click", applyFeedbackToFile);
    $.skipReview.addEventListener("click", closeReview);

    // Reset the output-format checkbox when the user picks a new file
    $.outputFormatMd.addEventListener("change", updateOutputFormatHint);

    window.addEventListener("beforeunload", stopPolling);

    // Selection-based annotation
    $.preview.addEventListener("mouseup", onTextSelect);

    // Show a banner describing which entity types the active privacy
    // filter is able to detect — most useful in mock mode where
    // names and addresses are not detected.
    loadModeInfo();
  }

  // ------------------------------------------------------------------
  //  Upload & status polling
  // ------------------------------------------------------------------

  function validateFile(file) {
    if (!/\.(md|docx)$/i.test(file.name)) {
      return "Поддерживаются только файлы .md и .docx";
    }
    if (file.size > MAX_FILE_SIZE) {
      return "Файл больше 20 МБ";
    }
    return null;
  }

  function fileExtension(name) {
    var m = /\.([^.]+)$/.exec(name);
    return m ? m[1].toLowerCase() : "";
  }

  function showUploadOptions(ext) {
    $.uploadOptions.hidden = false;
    if (ext === "docx") {
      // For .docx we keep the user's previous choice across
      // multiple uploads so that "перетащил следующий файл" doesn't
      // silently reset the format option.
      $.outputFormatMd.disabled = false;
      updateOutputFormatHint();
    } else {
      // For .md files the output is always .md — the checkbox is
      // disabled and pre-checked so the user can see the choice is
      // effectively locked.
      $.outputFormatMd.disabled = true;
      $.outputFormatMd.checked = true;
      updateOutputFormatHint();
    }
  }

  function updateOutputFormatHint() {
    if ($.outputFormatMd.disabled) {
      $.outputFormatHint.textContent =
        "Для Markdown-файлов формат результата совпадает с исходным.";
      return;
    }
    if ($.outputFormatMd.checked) {
      $.outputFormatHint.textContent =
        "Документ будет конвертирован в Markdown (через pandoc).";
    } else {
      $.outputFormatHint.textContent =
        "Файл останется в формате .docx с заменами PII.";
    }
  }

  function showError(message) {
    $.errorEl.hidden = false;
    $.errorEl.textContent = message;
    $.jobStatus.textContent = "Ошибка";
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
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && "message" in detail) return detail.message;
      if (typeof detail === "string") return detail;
      if (typeof body.error === "string") return body.error;
    }
    return null;
  }

  async function upload(file) {
    var err = validateFile(file);
    if (err) {
      showError(err);
      return;
    }

    closeReview();
    $.jobSection.hidden = false;
    $.jobFilename.textContent = file.name;
    $.jobStatus.textContent = "Загружается…";
    $.download.hidden = true;
    $.reviewBtn.hidden = true;
    $.errorEl.hidden = true;

    var ext = fileExtension(file.name);
    showUploadOptions(ext);

    var form = new FormData();
    form.append("file", file);
    // For .md files we always send output_format=md; for .docx we
    // honour the user checkbox.
    if (ext === "docx" && $.outputFormatMd.checked) {
      form.append("output_format", "md");
    } else if (ext === "md") {
      form.append("output_format", "md");
    }

    var res;
    try {
      res = await fetch("/api/v1/documents/", { method: "POST", body: form });
    } catch (e) {
      showError("Не удалось связаться с сервером");
      return;
    }

    if (!res.ok) {
      var message = "Ошибка загрузки (" + res.status + ")";
      if (res.status === 413) message = "Файл больше 20 МБ";
      else if (res.status === 400) message = "Неподдерживаемый формат файла";
      else {
        try {
          var body = await res.json();
          var detail = extractErrorDetail(body);
          if (detail) message = detail;
        } catch (_) {}
      }
      showError(message);
      return;
    }

    var job = await res.json();
    currentJobId = job.id;
    $.jobStatus.textContent = statusLabel(job.status);
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
    $.jobStatus.textContent = statusLabel(job.status);

    if (job.status === "completed") {
      stopPolling();
      var ext = job.output_ext || job.source_ext;
      $.download.href = "/api/v1/documents/" + currentJobId + "/download";
      $.download.download = "";
      $.download.setAttribute("data-ext", ext);
      $.download.hidden = false;
      $.reviewBtn.hidden = false;
      $.jobStatus.textContent = "Готово (" + ext + ")";
    } else if (job.status === "failed") {
      stopPolling();
      showError(job.error || "Обработка завершилась с ошибкой");
    }
  }

  function reset() {
    stopPolling();
    closeReview();
    currentJobId = null;
    $.jobSection.hidden = true;
    stopPolling();
    closeReview();
    currentJobId = null;
    $.jobSection.hidden = true;
    $.jobFilename.textContent = "—";
    $.jobStatus.textContent = "—";
    $.download.hidden = true;
    $.reviewBtn.hidden = true;
    $.download.removeAttribute("href");
    $.errorEl.hidden = true;
    $.fileInput.value = "";
    $.uploadOptions.hidden = true;
    // The "Результат в MD-формате" checkbox keeps its previous
    // selection across uploads so that dragging the next file
    // doesn't silently disable the user's preferred conversion.
  }

  // ------------------------------------------------------------------
  //  Mode info — describes which entity types the active privacy
  //  filter is able to detect.
  // ------------------------------------------------------------------

  function loadModeInfo() {
    fetch("/api/v1/mode")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (body) {
        if (!body) return;
        var mode = body.privacy_filter_mode;
        var detected = (body.detected_types || []).map(function (t) {
          return ENTITY_LABELS[t] || t;
        });
        var label = detected.join(", ");
        var msg;
        if (mode === "mock") {
          msg =
            "Режим mock — в этом режиме модель заменяет только " +
            label +
            ". Чтобы распознавались также ФИО и адреса, запустите сервис " +
            "с реальной моделью (NEIRONIR_PRIVACY_FILTER_MODE=subprocess).";
        } else {
          msg =
            "Режим " + mode + " — будут распознаны: " + label + ".";
        }
        $.modeInfoText.textContent = msg;
        $.modeInfoSection.hidden = false;
      })
      .catch(function () {
        // Endpoint may not be available on older deployments — the
        // banner is purely informational, so we silently skip.
      });
  }

  // ------------------------------------------------------------------
  //  Review UI
  // ------------------------------------------------------------------

  async function openReview() {
    if (!currentJobId) return;

    $.reviewSection.hidden = false;
    $.preview.textContent = "Загрузка...";
    $.confirmAll.hidden = false;
    $.submitFeedback.hidden = true;
    $.commentSection.hidden = true;
    $.feedbackSuccess.hidden = true;

    try {
      var res = await fetch("/api/v1/documents/" + currentJobId + "/annotations");
      if (!res.ok) throw new Error("HTTP " + res.status);
      reviewData = await res.json();
      pendingActions = [];
      renderPreview();
    } catch (e) {
      $.preview.textContent = "Не удалось загрузить данные для проверки: " + e.message;
    }
  }

  function closeReview() {
    $.reviewSection.hidden = true;
    $.preview.innerHTML = "";
    reviewData = null;
    pendingActions = [];
  }

  function renderPreview() {
    if (!reviewData) return;

    var text = reviewData.text;
    var spans = reviewData.spans;

    // If there was previously submitted feedback, mark those actions.
    var rejectedIndices = {};
    var addedSpans = [];
    pendingActions.forEach(function (a) {
      if (a.action === "reject" && a.original_span_index != null) {
        rejectedIndices[a.original_span_index] = true;
      }
      if (a.action === "add") {
        addedSpans.push(a);
      }
    });

    // Build highlighted HTML by walking the text and inserting <mark> spans.
    var allSpans = spans.map(function (s, i) {
      return { index: i, start: s.start, end: s.end, entity_type: s.entity_type, text: s.text, source: s.source, rejected: !!rejectedIndices[i] };
    });
    addedSpans.forEach(function (a) {
      allSpans.push({ index: -1, start: a.start, end: a.end, entity_type: a.entity_type, text: a.text, source: "user", added: true, rejected: false });
    });
    allSpans.sort(function (a, b) { return a.start - b.start || a.end - b.end; });

    var html = "";
    var pos = 0;
    allSpans.forEach(function (sp) {
      if (sp.start > pos) {
        html += escapeHtml(text.slice(pos, sp.start));
      }
      if (sp.end > pos) {
        var cls = "entity-span type-" + sp.entity_type;
        if (sp.rejected) cls += " rejected";
        if (sp.added) cls += " added";
        var label = ENTITY_LABELS[sp.entity_type] || sp.entity_type;
        html += '<span class="' + cls + '" data-index="' + sp.index + '" data-entity="' + sp.entity_type + '" title="' + label + '">' + escapeHtml(text.slice(Math.max(pos, sp.start), sp.end)) + "</span>";
        pos = sp.end;
      }
    });
    if (pos < text.length) {
      html += escapeHtml(text.slice(pos));
    }

    $.preview.innerHTML = html;

    // Attach click handler for rejecting spans.
    $.preview.querySelectorAll(".entity-span").forEach(function (el) {
      el.addEventListener("click", function (e) {
        var index = parseInt(el.dataset.index, 10);
        if (isNaN(index) || index < 0) return; // user-added spans
        toggleReject(el, index);
      });
    });
  }

  function toggleReject(el, index) {
    var existing = -1;
    pendingActions.forEach(function (a, i) {
      if (a.action === "reject" && a.original_span_index === index) existing = i;
    });

    if (existing >= 0) {
      // Undo reject
      pendingActions.splice(existing, 1);
      el.classList.remove("rejected");
    } else {
      pendingActions.push({
        action: "reject",
        start: reviewData.spans[index].start,
        end: reviewData.spans[index].end,
        entity_type: reviewData.spans[index].entity_type,
        text: reviewData.spans[index].text,
        original_span_index: index,
      });
      el.classList.add("rejected");
    }
    updateSubmitButton();
  }

  var _toolbarActive = false;

  function onTextSelect() {
    if (_toolbarActive) return;

    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !reviewData) return;

    // Only show toolbar if selection is inside the preview.
    var previewEl = $.preview;
    if (!previewEl.contains(sel.anchorNode) || !previewEl.contains(sel.focusNode)) return;

    var range = sel.getRangeAt(0);
    var text = range.toString().trim();
    if (text.length === 0) return;

    // Compute absolute offset in the full text.
    var start = getAbsoluteOffset(range.startContainer, range.startOffset, previewEl);
    var end = getAbsoluteOffset(range.endContainer, range.endOffset, previewEl);
    if (start < 0 || end < 0 || start >= end) return;

    showSelectionToolbar(sel, text, start, end);
  }

  function getAbsoluteOffset(node, offset, rootEl) {
    // Walk the text nodes to compute the absolute offset.
    var total = 0;
    var walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, null, false);
    var found = false;
    while (walker.nextNode()) {
      if (walker.currentNode === node) {
        found = true;
        break;
      }
      total += walker.currentNode.textContent.length;
    }
    if (!found) return -1;
    return total + offset;
  }

  function showSelectionToolbar(sel, text, start, end) {
    _toolbarActive = true;

    // Remove old toolbar
    var old = document.querySelector(".selection-toolbar");
    if (old) old.remove();

    var toolbar = document.createElement("div");
    toolbar.className = "selection-toolbar";

    var entityTypes = [
      ["private_person", "ФИО"],
      ["private_address", "Адрес"],
      ["private_email", "Email"],
      ["private_phone", "Телефон"],
      ["private_date", "Дата"],
      ["account_number", "Счёт/ИНН"],
      ["secret", "Секрет"],
    ];

    var span = document.createElement("span");
    span.textContent = "Пропущено:";
    span.style.marginRight = "4px";
    toolbar.appendChild(span);

    entityTypes.forEach(function (pair) {
      var btn = document.createElement("button");
      btn.textContent = pair[1];
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        toolbar.remove();
        sel.removeAllRanges();
        addEntity(text, start, end, pair[0]);
      });
      toolbar.appendChild(btn);
    });

    // Reset the guard shortly after the toolbar appears so the next
    // deliberate text selection still triggers the toolbar.
    setTimeout(function () { _toolbarActive = false; }, 200);

    document.body.appendChild(toolbar);

    // Position toolbar near the selection.  The toolbar is
    // ``position: fixed`` so we use viewport-relative coordinates
    // (``rect.bottom``) — adding ``window.scrollY`` would push the
    // toolbar far below the visible viewport when the user
    // highlighted text near the bottom of a long scrollable
    // preview.
    var rect = sel.getRangeAt(0).getBoundingClientRect();
    var top = rect.bottom + 4;
    var left = rect.left;
    // Clamp horizontally so the toolbar never goes off-screen on
    // narrow viewports.
    var maxLeft = window.innerWidth - 320;
    if (left > maxLeft) left = Math.max(8, maxLeft);
    // Clamp vertically.  If the selection is close to the bottom of
    // the viewport (or scrolled out of view inside a tall
    // scrollable container), show the toolbar above the selection
    // instead of below.
    var toolbarHeight = 36;
    if (top + toolbarHeight > window.innerHeight) {
      top = rect.top - toolbarHeight - 4;
      if (top < 0) top = Math.max(4, window.innerHeight - toolbarHeight);
    }
    // Final clamp: never let the toolbar leave the viewport
    // entirely.  This protects against ``getBoundingClientRect``
    // returning coordinates outside the viewport (e.g. when the
    // selection lives inside a scrollable container and is not
    // currently visible).
    if (top < 0) top = 4;
    if (top + toolbarHeight > window.innerHeight) {
      top = Math.max(4, window.innerHeight - toolbarHeight);
    }
    toolbar.style.top = top + "px";
    toolbar.style.left = left + "px";
  }

  function addEntity(text, start, end, entityType) {
    // Remove any existing ADD for the same range
    pendingActions = pendingActions.filter(function (a) {
      return !(a.action === "add" && a.start === start && a.end === end);
    });

    pendingActions.push({
      action: "add",
      start: start,
      end: end,
      entity_type: entityType,
      text: text,
      original_span_index: null,
    });
    updateSubmitButton();
    renderPreview(); // re-render to show the new span
  }

  function updateSubmitButton() {
    $.submitFeedback.hidden = pendingActions.length === 0;
    $.commentSection.hidden = pendingActions.length === 0;
  }

  // ------------------------------------------------------------------
  //  Submit feedback
  // ------------------------------------------------------------------

  async function confirmAll() {
    if (!reviewData || !currentJobId) return;

    // Mark all spans as confirmed
    var actions = reviewData.spans.map(function (sp, i) {
      return {
        action: "confirm",
        start: sp.start,
        end: sp.end,
        entity_type: sp.entity_type,
        text: sp.text,
        original_span_index: i,
      };
    });

    await postFeedback(actions, "");
  }

  async function submitFeedback() {
    if (!reviewData || !currentJobId) return;

    // Confirm all non-rejected spans first
    var rejectedIndices = {};
    pendingActions.forEach(function (a) {
      if (a.action === "reject" && a.original_span_index != null) {
        rejectedIndices[a.original_span_index] = true;
      }
    });

    var actions = [];
    reviewData.spans.forEach(function (sp, i) {
      if (rejectedIndices[i]) {
        actions.push({
          action: "reject",
          start: sp.start,
          end: sp.end,
          entity_type: sp.entity_type,
          text: sp.text,
          original_span_index: i,
        });
      } else {
        actions.push({
          action: "confirm",
          start: sp.start,
          end: sp.end,
          entity_type: sp.entity_type,
          text: sp.text,
          original_span_index: i,
        });
      }
    });

    // Add user-added spans
    pendingActions.forEach(function (a) {
      if (a.action === "add") {
        actions.push(a);
      }
    });

    var comment = $.feedbackComment.value.trim();
    await postFeedback(actions, comment);
  }

  // Build the same actions list as submitFeedback but POST to
  // ``/apply-feedback`` so the cleaned file is rewritten on disk.
  function collectFeedbackActions() {
    var rejectedIndices = {};
    pendingActions.forEach(function (a) {
      if (a.action === "reject" && a.original_span_index != null) {
        rejectedIndices[a.original_span_index] = true;
      }
    });

    var actions = [];
    if (reviewData && reviewData.spans) {
      reviewData.spans.forEach(function (sp, i) {
        if (rejectedIndices[i]) {
          actions.push({
            action: "reject",
            start: sp.start,
            end: sp.end,
            entity_type: sp.entity_type,
            text: sp.text,
            original_span_index: i,
          });
        } else {
          actions.push({
            action: "confirm",
            start: sp.start,
            end: sp.end,
            entity_type: sp.entity_type,
            text: sp.text,
            original_span_index: i,
          });
        }
      });
    }

    pendingActions.forEach(function (a) {
      if (a.action === "add") {
        actions.push(a);
      }
    });

    return actions;
  }

  async function applyFeedbackToFile() {
    if (!currentJobId) return;

    var actions = collectFeedbackActions();
    if (actions.length === 0) {
      showApplyError("Нет правок для применения.");
      return;
    }

    var comment = $.feedbackComment.value.trim();
    var previousDisabled = $.applyFeedback.disabled;
    $.applyFeedback.disabled = true;
    clearApplyMessages();

    try {
      var res = await fetch(
        "/api/v1/documents/" + currentJobId + "/apply-feedback",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actions: actions, comment: comment }),
        }
      );
      var body = null;
      try {
        body = await res.json();
      } catch (_) {
        body = null;
      }
      if (!res.ok) {
        var msg = "Не удалось применить правки";
        if (body && body.detail && body.detail.message) msg = body.detail.message;
        showApplyError(msg);
        return;
      }
      $.applySuccess.hidden = false;
      var trainingInfo = "";
      if (body.training_records_added > 0) {
        trainingInfo =
          " Добавлено " +
          body.training_records_added +
          " записей в датасет для обучения модели.";
      }
      $.applySuccess.textContent =
        "Правки применены к итоговому файлу: " +
        body.added + " добавлено, " +
        body.rejected + " отклонено, " +
        body.kept + " подтверждено." +
        trainingInfo;

      // Refresh the annotations so the user sees the updated state.
      try {
        var ann = await fetch("/api/v1/documents/" + currentJobId + "/annotations");
        if (ann.ok) {
          reviewData = await ann.json();
          pendingActions = [];
          renderPreview();
        }
      } catch (_) {}
    } catch (e) {
      showApplyError("Сеть: " + e.message);
    } finally {
      $.applyFeedback.disabled = previousDisabled;
    }
  }

  function showApplyError(msg) {
    $.applyError.hidden = false;
    $.applyError.textContent = msg;
  }

  function clearApplyMessages() {
    $.applySuccess.hidden = true;
    $.applyError.hidden = true;
    $.applySuccess.textContent = "Правки применены к итоговому файлу.";
  }

  async function postFeedback(actions, comment) {
    if (!currentJobId) return;

    try {
      var res = await fetch("/api/v1/documents/" + currentJobId + "/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actions: actions, comment: comment }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
    } catch (e) {
      alert("Не удалось сохранить правки: " + e.message);
      return;
    }

    // Show success message
    $.feedbackSuccess.hidden = false;
    $.confirmAll.hidden = true;
    $.submitFeedback.hidden = true;
    $.skipReview.hidden = true;
    $.commentSection.hidden = true;
    pendingActions = [];
  }

  // ------------------------------------------------------------------
  //  Utilities
  // ------------------------------------------------------------------

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  // Init
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
