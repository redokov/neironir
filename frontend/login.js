/**
 * Login page logic for the neironir admin interface.
 *
 * Shows inline errors from server-side redirects and intercepts the
 * login form to display network/validation errors without a full page
 * reload.
 */

(function () {
  "use strict";

  // Show an inline error if the server redirected here with ?error=invalid.
  var params = new URLSearchParams(window.location.search);
  var err = params.get("error");
  if (err === "invalid") {
    var node = document.getElementById("login-error");
    if (node) {
      node.textContent = "Неверный логин или пароль.";
      node.hidden = false;
    }
  }

  // Intercept the form submit and use fetch so we can show inline
  // errors without a full page reload. On success the server
  // replies with 303 —> Location: <next>; we follow it.
  document.getElementById("login-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var form = ev.currentTarget;
    var data = new FormData(form);
    var errNode = document.getElementById("login-error");
    errNode.hidden = true;
    errNode.textContent = "";

    var searchParams = new URLSearchParams(window.location.search);
    var next = searchParams.get("next") || "/admin";

    fetch("/login?next=" + encodeURIComponent(next), {
      method: "POST",
      body: data,
      credentials: "same-origin",
      redirect: "manual",
    }).then(function (resp) {
      if (resp.status === 0 || (resp.status >= 200 && resp.status < 400)) {
        window.location.href = next;
        return;
      }
      if (resp.status === 303 || resp.status === 302 || resp.status === 307) {
        window.location.href = next;
        return;
      }
      errNode.textContent = "Не удалось войти (код " + resp.status + ").";
      errNode.hidden = false;
    }).catch(function () {
      errNode.textContent = "Сетевая ошибка.";
      errNode.hidden = false;
    });
  });
})();
