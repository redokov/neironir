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
    $.skipReview = document.getElementById("skip-review");
    $.commentSection = document.getElementById("comment-section");
    $.feedbackComment = document.getElementById("feedback-comment");
    $.feedbackSuccess = document.getElementById("feedback-success");

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
    $.skipReview.addEventListener("click", closeReview);

    window.addEventListener("beforeunload", stopPolling);

    // Selection-based annotation
    $.preview.addEventListener("mouseup", onTextSelect);
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

    var form = new FormData();
    form.append("file", file);

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
      $.download.href = "/api/v1/documents/" + currentJobId + "/download";
      $.download.hidden = false;
      $.reviewBtn.hidden = false;
      $.jobStatus.textContent = "Готово";
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
    $.jobFilename.textContent = "—";
    $.jobStatus.textContent = "—";
    $.download.hidden = true;
    $.reviewBtn.hidden = true;
    $.download.removeAttribute("href");
    $.errorEl.hidden = true;
    $.fileInput.value = "";
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

    // Position toolbar near selection
    var rect = sel.getRangeAt(0).getBoundingClientRect();
    var top = rect.bottom + window.scrollY + 4;
    var left = rect.left + window.scrollX;
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
