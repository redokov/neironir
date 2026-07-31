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
  var abortController = null;
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
    $.processingNote = document.getElementById("processing-note");
    $.resetBtn = document.getElementById("reset");
    $.reviewBtn = document.getElementById("review-btn");
    $.reviewSection = document.getElementById("review-section");
    $.preview = document.getElementById("preview");
    $.applyFeedback = document.getElementById("apply-feedback");
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
    $.applyFeedback.addEventListener("click", applyFeedbackToFile);

    // Reset the output-format checkbox when the user picks a new file
    $.outputFormatMd.addEventListener("change", function () {
      $.outputFormatMd.dataset.userSet = "true";
      updateOutputFormatHint();
    });

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
      // If the checkbox has never been touched, default to checked.
      if (!$.outputFormatMd.dataset.userSet) {
        $.outputFormatMd.checked = true;
      }
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
    // Abort any in-flight poll from a previous upload.
    if (abortController) abortController.abort();
    abortController = null;

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
    // Capture the job ID at the start of the poll so that if the user
    // quickly uploads a new file while this fetch is in flight, we
    // don't write the *old* job's status over the *new* job's UI.
    var myJobId = currentJobId;

    // Create a new AbortController for this poll interval, abort the
    // previous one if it's still running.
    if (abortController) abortController.abort();
    abortController = new AbortController();
    var signal = abortController.signal;

    var res;
    try {
      res = await fetch("/api/v1/documents/" + myJobId, { signal: signal });
    } catch (e) {
      if (e.name === "AbortError") return; // job was replaced, ignore
      stopPolling();
      showError("Не удалось получить статус задачи");
      return;
    }
    // If the job was replaced while we were fetching, ignore the result.
    if (myJobId !== currentJobId) return;

    if (!res.ok) {
      stopPolling();
      showError("Не удалось получить статус задачи");
      return;
    }
    var job = await res.json();
    if (myJobId !== currentJobId) return;
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
      // Show processing note if present (e.g. fallback from neural to mock).
      if (job.processing_note) {
        $.processingNote.textContent = job.processing_note;
        $.processingNote.hidden = false;
      }
    } else if (job.status === "failed") {
      stopPolling();
      showError(job.error || "Обработка завершилась с ошибкой");
    }
  }

  function reset() {
    stopPolling();
    if (abortController) abortController.abort();
    abortController = null;
    closeReview();
    currentJobId = null;
    $.jobSection.hidden = true;
    $.jobFilename.textContent = "—";
    $.jobStatus.textContent = "—";
    $.download.hidden = true;
    $.reviewBtn.hidden = true;
    $.download.removeAttribute("href");
    $.errorEl.hidden = true;
    $.processingNote.hidden = true;
    $.processingNote.textContent = "";
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
    clearApplyMessages();

    try {
      var res = await fetch("/api/v1/documents/" + currentJobId + "/annotations");
      if (!res.ok) throw new Error("HTTP " + res.status);
      reviewData = await res.json();
      pendingActions = [];
      renderPreview();
      updateApplyButton();
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

    // Build highlighted HTML by walking the text and creating <mark> spans
    // using DOM APIs so attribute values are never interpolated unsafely.
    var allSpans = spans.map(function (s, i) {
      return { index: i, start: s.start, end: s.end, entity_type: s.entity_type, text: s.text, source: s.source, rejected: !!rejectedIndices[i] };
    });
    addedSpans.forEach(function (a) {
      allSpans.push({ index: -1, start: a.start, end: a.end, entity_type: a.entity_type, text: a.text, source: "user", added: true, rejected: false });
    });
    allSpans.sort(function (a, b) { return a.start - b.start || a.end - b.end; });

    // Clear the preview container and rebuild using DOM methods.
    $.preview.textContent = "";

    var pos = 0;
    allSpans.forEach(function (sp) {
      if (sp.start > pos) {
        $.preview.appendChild(document.createTextNode(text.slice(pos, sp.start)));
      }
      if (sp.end > pos) {
        var span = document.createElement("span");
        var cls = "entity-span type-" + sp.entity_type;
        if (sp.rejected) cls += " rejected";
        if (sp.added) cls += " added";
        span.className = cls;
        span.dataset.index = String(sp.index);
        span.dataset.entity = sp.entity_type;
        var label = ENTITY_LABELS[sp.entity_type] || sp.entity_type;
        span.title = label;
        span.textContent = text.slice(Math.max(pos, sp.start), sp.end);
        $.preview.appendChild(span);
        pos = sp.end;
      }
    });
    if (pos < text.length) {
      $.preview.appendChild(document.createTextNode(text.slice(pos)));
    }

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
    updateApplyButton();
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
    updateApplyButton();
    renderPreview(); // re-render to show the new span
  }

  function updateApplyButton() {
    if (!$.applyFeedback) return;
    var hasSpans = !!(reviewData && reviewData.spans && reviewData.spans.length > 0);
    var hasActions = pendingActions.length > 0;
    $.applyFeedback.disabled = !(hasSpans || hasActions);
  }

  // Mirror of PlaceholderCounter.next() on the server side. Picks the
  // next free <TYPE_N> for the given entity_type by scanning the current
  // text for the largest existing N. ``extraTaken`` lists numbers already
  // issued in the same batch (multiple add-actions of one type), so each
  // add gets a unique placeholder. See backend/neironir/domain/placeholder.py.
  function nextPlaceholderForType(text, entityType, extraTaken) {
    var typeToPrefix = {
      private_person: "PRIVATE_PERSON",
      private_address: "PRIVATE_ADDRESS",
      private_email: "PRIVATE_EMAIL",
      private_phone: "PRIVATE_PHONE",
      private_date: "PRIVATE_DATE",
      private_url: "PRIVATE_URL",
      account_number: "ACCOUNT_NUMBER",
      secret: "SECRET",
    };
    var prefix = typeToPrefix[entityType] || "UNKNOWN";
    var re = new RegExp("<" + prefix + "(\\d+)>", "g");
    var maxN = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      var n = parseInt(m[1], 10);
      if (n > maxN) maxN = n;
    }
    (extraTaken || []).forEach(function (taken) {
      if (taken > maxN) maxN = taken;
    });
    return "<" + prefix + (maxN + 1) + ">";
  }

  // ------------------------------------------------------------------
  //  Apply feedback
  // ------------------------------------------------------------------

  // Build the list of actions to send to /apply-feedback so the
  // cleaned file is rewritten on disk.
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

    var previousDisabled = $.applyFeedback.disabled;
    $.applyFeedback.disabled = true;
    clearApplyMessages();

    try {
      var res = await fetch(
        "/api/v1/documents/" + currentJobId + "/apply-feedback",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actions: actions }),
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

      // Locally apply the changes to reviewData so the preview reflects
      // the same state as the rewritten result file. The server doesn't
      // return updated annotations, and re-fetching GET /annotations
      // would return the stale original data (annotations.json is not
      // rewritten by apply-feedback). See spec
      // .ai/sdd/specs/001-apply-feedback-to-result/design.md §3.1.
      var submitted = actions;
      var submittedAddKeys = {};
      var submittedRejectIndices = {};
      submitted.forEach(function (a) {
        if (a.action === "add") {
          submittedAddKeys[
            "add:" + a.start + ":" + a.end + ":" + a.entity_type
          ] = true;
        } else if (
          a.action === "reject" &&
          a.original_span_index != null
        ) {
          submittedRejectIndices[a.original_span_index] = a;
        }
      });

      if (reviewData) {
        // Drop rejected spans from reviewData.spans; track the original
        // text so we can splice it back into reviewData.text below.
        var keptSpans = [];
        var rejectedSplices = []; // {start, end, text}
        reviewData.spans.forEach(function (sp, i) {
          if (submittedRejectIndices[i]) {
            var action = submittedRejectIndices[i];
            rejectedSplices.push({
              start: sp.start,
              end: sp.end,
              text: action.text != null ? action.text : sp.text,
            });
          } else {
            keptSpans.push(sp);
          }
        });
        reviewData.spans = keptSpans;

        // Apply splices in reverse so earlier offsets stay valid.
        rejectedSplices.sort(function (a, b) {
          return b.start - a.start;
        });

        // Insert new placeholders for add-actions and remove the
        // underlying original text. The placeholder is built using
        // nextPlaceholderForType() which mirrors the server's
        // PlaceholderCounter logic (see backend/neironir/domain/placeholder.py).
        var addSplices = []; // {start, end, replacement, entity_type, text}
        var batchTaken = {}; // entity_type -> numbers issued in this batch
        submitted.forEach(function (a) {
          if (a.action === "add") {
            var taken = batchTaken[a.entity_type] || [];
            var replacement = nextPlaceholderForType(
              reviewData.text,
              a.entity_type,
              taken
            );
            // Extract the number from the placeholder to seed the batch
            // counter for subsequent add-actions of the same type.
            var m2 = replacement.match(/(\d+)/);
            if (m2) taken.push(parseInt(m2[1], 10));
            batchTaken[a.entity_type] = taken;
            addSplices.push({
              start: a.start,
              end: a.end,
              replacement: replacement,
              entity_type: a.entity_type,
              text: a.text,
            });
          }
        });
        // Apply all splices (rejected + add) together, sorted by start desc.
        var allSplices = rejectedSplices
          .map(function (s) {
            return {
              start: s.start,
              end: s.end,
              replacement: s.text,
            };
          })
          .concat(
            addSplices.map(function (s) {
              return {
                start: s.start,
                end: s.end,
                replacement: s.replacement,
              };
            })
          );
        allSplices.sort(function (a, b) {
          return b.start - a.start;
        });
        var newText = reviewData.text;
        allSplices.forEach(function (s) {
          newText =
            newText.slice(0, s.start) + s.replacement + newText.slice(s.end);
        });
        reviewData.text = newText;

        // Append the new add-spans to reviewData.spans so renderPreview
        // highlights them. Positions are recomputed by re-walking the
        // placeholder regex in renderPreview, so approximate positions
        // are fine here.
        addSplices.forEach(function (s) {
          var idx = newText.indexOf(s.replacement);
          reviewData.spans.push({
            start: idx,
            end: idx + s.replacement.length,
            entity_type: s.entity_type,
            text: s.replacement,
            source: "user",
          });
        });
      }

      // Drop submitted add/reject actions from pendingActions; keep any
      // that were added after this apply call.
      // Keep only pending actions that were NOT in the just-submitted
      // batch — these are actions the user added *after* the last
      // apply-feedback call (they'll be submitted next time).
      pendingActions = pendingActions.filter(function (pa) {
        if (pa.action === "add") {
          var k = "add:" + pa.start + ":" + pa.end + ":" + pa.entity_type;
          return !submittedAddKeys[k];
        }
        if (pa.action === "reject" && pa.original_span_index != null) {
          return !submittedRejectIndices[pa.original_span_index];
        }
        return false;
      });

      updateApplyButton();
      renderPreview();
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
