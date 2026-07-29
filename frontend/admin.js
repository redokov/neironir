// neironir admin dashboard — stats, training, feedback, rules.
//
// Talks to the JSON API under /api/v1/admin/* and polls the training
// status while a run is in flight.

(function () {
  "use strict";

  var TRAINING_POLL_MS = 1500;
  var TRAINING_POLL_UNTIL_IDLE = false;

  // --- CSRF helpers (double-submit cookie pattern) ---------------------
  // The server sets a non-HttpOnly cookie named NEIRONIR_CSRF_COOKIE
  // (default "neironir_csrf"). We read it and send the same value back
  // in the X-CSRF-Token header on every unsafe request.
  function getCsrfCookieName() {
    var meta = document.querySelector("meta[name='neironir-csrf-cookie']");
    return meta ? meta.getAttribute("content") : "neironir_csrf";
  }

  function readCookie(name) {
    var prefix = name + "=";
    var parts = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < parts.length; i++) {
      var c = parts[i].replace(/^\s+/, "");
      if (c.indexOf(prefix) === 0) {
        return decodeURIComponent(c.substring(prefix.length));
      }
    }
    return "";
  }

  function csrfHeaders(extra) {
    var headers = Object.assign({}, extra || {});
    var token = readCookie(getCsrfCookieName());
    if (token) {
      headers["X-CSRF-Token"] = token;
    }
    return headers;
  }

  function fetchCsrf(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    if (options.method && options.method.toUpperCase() !== "GET") {
      options.headers = csrfHeaders(options.headers || {});
    }
    return fetch(url, options).then(function (resp) {
      if (resp.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
        throw new Error("unauthenticated");
      }
      if (resp.status === 403) {
        // Could be CSRF mismatch — surface to the user.
        alert("Доступ запрещён (403). Возможно, сессия истекла — войдите заново.");
        throw new Error("forbidden");
      }
      return resp;
    });
  }

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

  var $ = {};
  var currentJobId = null;
  var trainingPollTimer = null;
  var lastTrainingStatus = "idle";

  function init() {
    $ = {
      statTotal: document.getElementById("stat-total"),
      statCompleted: document.getElementById("stat-completed"),
      statFailed: document.getElementById("stat-failed"),
      statFeedback: document.getElementById("stat-feedback"),
      statsPeriod: document.getElementById("stats-period"),
      statsDays: document.getElementById("stats-days"),
      refreshStats: document.getElementById("refresh-stats"),
      statsBuckets: document.getElementById("stats-buckets"),

      trainEpochs: document.getElementById("train-epochs"),
      trainStart: document.getElementById("train-start"),
      trainStop: document.getElementById("train-stop"),
      trainStatus: document.getElementById("train-status-value"),
      trainProgress: document.getElementById("train-progress"),
      trainEta: document.getElementById("train-eta"),
      trainError: document.getElementById("train-error"),
      trainLog: document.getElementById("train-log"),

      feedbackRows: document.getElementById("feedback-rows"),
      detailSection: document.getElementById("detail-section"),
      detailMeta: document.getElementById("detail-meta"),
      detailText: document.getElementById("detail-text"),
      detailFeedback: document.getElementById("detail-feedback"),
      detailClose: document.getElementById("detail-close"),

      rulesMin: document.getElementById("rules-min"),
      rulesGenerate: document.getElementById("rules-generate"),
      rulesRefresh: document.getElementById("rules-refresh"),
      rulesRows: document.getElementById("rules-rows"),

      globalError: document.getElementById("global-error"),
    };

    $.refreshStats.addEventListener("click", function () {
      loadStats();
    });
    $.statsPeriod.addEventListener("change", loadStats);
    $.statsDays.addEventListener("change", loadStats);

    $.trainStart.addEventListener("click", startTraining);
    $.trainStop.addEventListener("click", stopTraining);

    $.detailClose.addEventListener("click", function () {
      $.detailSection.hidden = true;
      currentJobId = null;
    });

    $.rulesGenerate.addEventListener("click", generateProposals);
    $.rulesRefresh.addEventListener("click", loadRules);

    // Logout button
    var logoutBtn = document.getElementById("logout-button");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", function () {
        var token = readCookie(getCsrfCookieName());
        fetch("/logout", {
          method: "POST",
          headers: token ? { "X-CSRF-Token": token } : {},
          credentials: "same-origin",
          redirect: "manual",
        }).then(function () {
          window.location.href = "/login";
        }).catch(function () {
          window.location.href = "/login";
        });
      });
    }

    loadStats();
    loadDocuments();
    loadRules();
    pollTrainingStatus();
  }

  // -------------------------------------------------------------------
  // Stats
  // -------------------------------------------------------------------

  function loadStats() {
    var period = $.statsPeriod.value;
    var days = parseInt($.statsDays.value, 10) || 30;
    fetchCsrf(
      "/api/v1/admin/stats?period=" +
        encodeURIComponent(period) +
        "&days=" +
        encodeURIComponent(days)
    )
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (res.status !== 200) {
          showGlobalError("Не удалось загрузить статистику: " + JSON.stringify(res.body));
          return;
        }
        $.statTotal.textContent = res.body.total_jobs;
        $.statCompleted.textContent = res.body.completed_jobs;
        $.statFailed.textContent = res.body.failed_jobs;
        $.statFeedback.textContent = res.body.jobs_with_feedback;

        var buckets = res.body.by_day || {};
        var keys = Object.keys(buckets).sort();
        if (keys.length === 0) {
          $.statsBuckets.textContent = "— нет данных —";
        } else {
          $.statsBuckets.innerHTML = keys
            .map(function (k) {
              return '<div class="bucket-row"><span class="bucket-key">' +
                escapeHtml(k) +
                '</span><span class="bucket-count">' +
                buckets[k] +
                "</span></div>";
            })
            .join("");
        }
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  // -------------------------------------------------------------------
  // Documents / Feedback
  // -------------------------------------------------------------------

  function loadDocuments() {
    fetchCsrf("/api/v1/admin/documents?limit=50")
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        if (!Array.isArray(rows) || rows.length === 0) {
          $.feedbackRows.innerHTML =
            '<tr><td colspan="7" class="empty">— нет данных —</td></tr>';
          return;
        }
        $.feedbackRows.innerHTML = rows
          .map(function (row) {
            var dt = row.finished_at || row.created_at || "";
            return "<tr data-job='" + escapeAttr(row.job_id) + "' class='feedback-row'>" +
              "<td>" + escapeHtml(row.source_filename) + "</td>" +
              "<td>" + escapeHtml(formatDate(dt)) + "</td>" +
              "<td>" + escapeHtml(row.status) + "</td>" +
              "<td>" + (row.detected_spans || 0) + "</td>" +
              "<td>" + (row.confirmed || 0) + "</td>" +
              "<td>" + (row.rejected || 0) + "</td>" +
              "<td>" + (row.added || 0) + "</td>" +
              "</tr>";
          })
          .join("");
        Array.prototype.forEach.call(
          $.feedbackRows.querySelectorAll(".feedback-row"),
          function (tr) {
            tr.addEventListener("click", function () {
              openDetail(tr.getAttribute("data-job"));
            });
          }
        );
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  function openDetail(jobId) {
    currentJobId = jobId;
    fetchCsrf("/api/v1/admin/documents/" + encodeURIComponent(jobId))
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (res.status !== 200) {
          showGlobalError("Не удалось загрузить документ: " + JSON.stringify(res.body));
          return;
        }
        renderDetail(res.body);
        $.detailSection.hidden = false;
        $.detailSection.scrollIntoView({ behavior: "smooth" });
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  function renderDetail(payload) {
    var job = payload.job || {};
    $.detailMeta.innerHTML =
      "<dl class='detail-meta-dl'>" +
      "<dt>Файл</dt><dd>" + escapeHtml(job.source_filename || "") + "</dd>" +
      "<dt>ID</dt><dd>" + escapeHtml(payload.job_id || "") + "</dd>" +
      "<dt>Статус</dt><dd>" + escapeHtml(job.status || "") + "</dd>" +
      "<dt>Создан</dt><dd>" + escapeHtml(formatDate(job.created_at)) + "</dd>" +
      "<dt>Завершён</dt><dd>" + escapeHtml(formatDate(job.finished_at)) + "</dd>" +
      (job.error ? "<dt>Ошибка</dt><dd>" + escapeHtml(job.error) + "</dd>" : "") +
      "</dl>";

    var text = payload.text || "";
    var spans = (payload.annotations || []).slice().sort(function (a, b) {
      return a.start - b.start;
    });
    $.detailText.innerHTML = renderHighlightedText(text, spans);

    var feedback = payload.feedback;
    if (!feedback) {
      $.detailFeedback.innerHTML =
        "<p class='hint'>Обратная связь не оставлена.</p>";
      return;
    }
    var actions = feedback.actions || [];
    var html = "<ul class='feedback-actions'>";
    html += "<li><strong>Всего действий:</strong> " + actions.length + "</li>";
    if (feedback.comment) {
      html += "<li><strong>Комментарий:</strong> " + escapeHtml(feedback.comment) + "</li>";
    }
    for (var i = 0; i < actions.length; i++) {
      var a = actions[i];
      html += "<li>" +
        "<span class='tag tag-" + escapeAttr(tagClass(a.entity_type)) + "'>" +
        escapeHtml(ENTITY_LABELS[a.entity_type] || a.entity_type) +
        "</span> " +
        "<code>" + escapeHtml(a.action) + "</code> " +
        "<span>" + escapeHtml(a.text) + "</span>" +
        " <span class='hint'>(" + a.start + "—" + a.end + ")</span>" +
        "</li>";
    }
    html += "</ul>";
    $.detailFeedback.innerHTML = html;
  }

  function renderHighlightedText(text, spans) {
    if (!spans.length) return escapeHtml(text);
    var out = "";
    var pos = 0;
    for (var i = 0; i < spans.length; i++) {
      var s = spans[i];
      if (s.start > pos) {
        out += escapeHtml(text.slice(pos, s.start));
      }
      out += "<mark class='tag tag-" + escapeAttr(tagClass(s.entity_type)) +
        "' title='" + escapeAttr(s.entity_type) + " (" + s.start + "—" + s.end + ")'>" +
        escapeHtml(text.slice(s.start, s.end)) + "</mark>";
      pos = s.end;
    }
    if (pos < text.length) out += escapeHtml(text.slice(pos));
    return out;
  }

  // -------------------------------------------------------------------
  // Training
  // -------------------------------------------------------------------

  function startTraining() {
    clearError($.trainError);
    var epochs = parseInt($.trainEpochs.value, 10) || 3;
    $.trainStart.disabled = true;
    fetchCsrf("/api/v1/admin/training/start?epochs=" + encodeURIComponent(epochs), {
      method: "POST",
    })
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (res.status === 200) {
          applyTrainingStatus(res.body);
          ensureTrainingPolling();
        } else {
          $.trainStart.disabled = false;
          var msg = (res.body && res.body.detail && res.body.detail.message) ||
            JSON.stringify(res.body);
          showFieldError($.trainError, msg);
        }
      })
      .catch(function (err) {
        $.trainStart.disabled = false;
        showFieldError($.trainError, err.message);
      });
  }

  function stopTraining() {
    fetchCsrf("/api/v1/admin/training/stop", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        $.trainStop.disabled = true;
        showFieldError($.trainError, "Отправлен сигнал остановки…");
      })
      .catch(function (err) {
        showFieldError($.trainError, err.message);
      });
  }

  function pollTrainingStatus() {
    if (trainingPollTimer) return;
    trainingPollTimer = setInterval(function () {
      fetchCsrf("/api/v1/admin/training/status")
        .then(function (r) { return r.json(); })
        .then(function (body) {
          applyTrainingStatus(body);
          if (body.status !== "running" && lastTrainingStatus === "running") {
            // Just transitioned out of running — leave polling on, but the
            // next manual refresh will see the final state.
          }
          lastTrainingStatus = body.status;
        })
        .catch(function () {
          /* ignore transient network errors */
        });
    }, TRAINING_POLL_MS);
  }

  function ensureTrainingPolling() {
    if (!trainingPollTimer) pollTrainingStatus();
  }

  function applyTrainingStatus(state) {
    if (!state) return;
    $.trainStatus.textContent = state.status || "—";
    var prog = state.progress || {};
    var epoch = prog.epoch || 0;
    var total = prog.total_epochs || 0;
    var loss = prog.loss == null ? "—" : Number(prog.loss).toFixed(4);
    $.trainProgress.textContent = epoch + " / " + total + " эпох, loss=" + loss;
    var eta = prog.eta_seconds;
    $.trainEta.textContent = eta == null ? "—" : formatSeconds(eta);

    $.trainLog.textContent = (state.log_tail || []).slice(-50).join("\n") || "—";

    var running = state.status === "running";
    $.trainStart.disabled = running;
    $.trainStop.disabled = !running;
    if (state.error) {
      showFieldError($.trainError, state.error);
    } else if (state.status !== "running") {
      clearError($.trainError);
    }
  }

  // -------------------------------------------------------------------
  // Rules
  // -------------------------------------------------------------------

  function loadRules() {
    fetchCsrf("/api/v1/rules")
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        if (!Array.isArray(rows) || rows.length === 0) {
          $.rulesRows.innerHTML =
            '<tr><td colspan="6" class="empty">— нет правил —</td></tr>';
          return;
        }
        $.rulesRows.innerHTML = rows
          .map(function (r) {
            var actions =
              r.status === "proposed"
                ? "<button class='button small rule-approve' data-id='" +
                  escapeAttr(r.rule_id) +
                  "'>✓</button>" +
                  "<button class='button small rule-reject' data-id='" +
                  escapeAttr(r.rule_id) +
                  "'>✗</button>"
                : "";
            return "<tr>" +
              "<td>" + escapeHtml(ENTITY_LABELS[r.entity_type] || r.entity_type) + "</td>" +
              "<td><code>" + escapeHtml(r.pattern) + "</code></td>" +
              "<td>" + (r.evidence_count || 0) + "</td>" +
              "<td>" + (r.confidence != null ? Number(r.confidence).toFixed(2) : "—") + "</td>" +
              "<td><span class='status-pill status-" + escapeAttr(r.status) + "'>" +
                escapeHtml(r.status) + "</span></td>" +
              "<td>" + actions + "</td>" +
              "</tr>";
          })
          .join("");
        Array.prototype.forEach.call(
          $.rulesRows.querySelectorAll(".rule-approve"),
          function (btn) {
            btn.addEventListener("click", function () {
              decideRule(btn.getAttribute("data-id"), "approve");
            });
          }
        );
        Array.prototype.forEach.call(
          $.rulesRows.querySelectorAll(".rule-reject"),
          function (btn) {
            btn.addEventListener("click", function () {
              decideRule(btn.getAttribute("data-id"), "reject");
            });
          }
        );
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  function generateProposals() {
    var min = parseInt($.rulesMin.value, 10) || 3;
    fetchCsrf(
      "/api/v1/rules/proposals?min_occurrences=" + encodeURIComponent(min),
      { method: "POST" }
    )
      .then(function (r) { return r.json(); })
      .then(function () {
        loadRules();
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  function decideRule(ruleId, decision) {
    fetchCsrf("/api/v1/rules/" + encodeURIComponent(ruleId) + "/" + decision, {
      method: "POST",
    })
      .then(function (r) {
        if (!r.ok) {
          showGlobalError("Не удалось " + decision + " правило");
          return;
        }
        loadRules();
      })
      .catch(function (err) {
        showGlobalError("Сеть: " + err.message);
      });
  }

  // -------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------

  function tagClass(entityType) {
    switch (entityType) {
      case "private_person":
        return "person";
      case "private_address":
        return "address";
      case "private_email":
        return "email";
      case "private_phone":
        return "phone";
      case "private_date":
        return "date";
      case "private_url":
        return "url";
      case "account_number":
        return "account";
      case "secret":
        return "secret";
      default:
        return "default";
    }
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      return d.toLocaleString("ru-RU");
    } catch (_e) {
      return iso;
    }
  }

  function formatSeconds(secs) {
    if (secs == null) return "—";
    var h = Math.floor(secs / 3600);
    var m = Math.floor((secs % 3600) / 60);
    var s = secs % 60;
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return h + ":" + pad(m) + ":" + pad(s);
  }

  function showGlobalError(msg) {
    $.globalError.hidden = false;
    $.globalError.textContent = msg;
  }

  function showFieldError(el, msg) {
    el.hidden = false;
    el.textContent = msg;
  }

  function clearError(el) {
    el.hidden = true;
    el.textContent = "";
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  // --- Settings ------------------------------------------------------

  var $settingsTimeout = document.getElementById("settings-timeout");
  var $settingsSave = document.getElementById("settings-save");
  var $settingsStatus = document.getElementById("settings-status");

  function loadSettings() {
    fetch("/api/v1/admin/settings", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.privacy_filter_timeout != null) {
          $settingsTimeout.value = data.privacy_filter_timeout;
        }
      })
      .catch(function () { /* settings not critical */ });
  }

  function saveSettings() {
    var timeout = parseInt($settingsTimeout.value, 10);
    if (isNaN(timeout) || timeout < 10 || timeout > 86400) {
      showStatus($settingsStatus, "Таймаут должен быть от 10 до 86400 секунд.", true);
      return;
    }
    $settingsSave.disabled = true;
    fetchCsrf("/api/v1/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ privacy_filter_timeout: timeout }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function () {
        showStatus($settingsStatus, "Сохранено (" + timeout + " с).", false);
      })
      .catch(function () {
        showStatus($settingsStatus, "Ошибка сохранения.", true);
      })
      .finally(function () {
        $settingsSave.disabled = false;
      });
  }

  function showStatus(el, msg, isError) {
    el.textContent = msg;
    el.className = isError ? "error" : "success";
    el.hidden = false;
    setTimeout(function () { el.hidden = true; }, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init();
      loadSettings();
    });
  } else {
    init();
    loadSettings();
  }

  if ($settingsSave) {
    $settingsSave.addEventListener("click", saveSettings);
  }
})();