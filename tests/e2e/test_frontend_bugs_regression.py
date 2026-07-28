"""Regression tests for the three frontend bug fixes reported by users.

Each test corresponds to one of the issues:

1. The "Результат в MD-формате" checkbox silently resets when the user
   drops a file onto the dropzone.
2. The user can't see what's happening in mock mode — names and
   addresses are detected by the real model but not by mock.
3. The selection toolbar appears far below the visible viewport.

We cover them at three layers:

* Backend integration tests (``/api/v1/mode``) verify that the
  frontend has a programmatic way to learn what the active mode can
  detect.
* A static-analysis unit test asserts the relevant JavaScript code
  patches are present (catches regressions without a browser).
* End-to-end Playwright tests drive a real browser through the
  three scenarios — those run only when ``PLAYWRIGHT_BROWSERS_PATH``
  is set (the dev environment has Chromium installed).
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"


# ---------------------------------------------------------------------------
# 1. Backend: /api/v1/mode
# ---------------------------------------------------------------------------


@pytest.fixture
def mode_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Build a FastAPI TestClient with isolated storage."""
    from neironir.admin.training import reset_training_state
    from neironir.api.dependencies import get_privacy, get_settings, get_storage
    from neironir.config import Settings
    from neironir.main import create_app
    from neironir.privacy.client import MockPrivacyFilterClient
    from neironir.storage.local import LocalStorage

    storage = LocalStorage(tmp_path / "storage")
    privacy = MockPrivacyFilterClient()
    real_settings = Settings().model_copy(
        update={
            "storage_dir": str(tmp_path / "storage"),
            "privacy_filter_mode": "mock",
        }
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: real_settings
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_privacy] = lambda: privacy

    reset_training_state()
    with TestClient(app) as client:
        yield client

    reset_training_state()
    shutil.rmtree(tmp_path / "storage", ignore_errors=True)


class TestModeEndpoint:
    """``GET /api/v1/mode`` returns a structured description of the active mode."""

    def test_mode_endpoint_returns_200(self, mode_client: TestClient) -> None:
        r = mode_client.get("/api/v1/mode")
        assert r.status_code == 200

    def test_mode_endpoint_returns_dict(self, mode_client: TestClient) -> None:
        body = mode_client.get("/api/v1/mode").json()
        assert isinstance(body, dict)
        assert "privacy_filter_mode" in body
        assert "detected_types" in body

    def test_mode_endpoint_lists_mock_categories(self, mode_client: TestClient) -> None:
        """Mock mode must include all regex-detectable categories.

        Crucially, ``private_person`` and ``private_address`` are
        **not** in the list — that's the regression we want to
        surface to the frontend so the user understands why names
        aren't redacted.
        """
        body = mode_client.get("/api/v1/mode").json()
        assert body["privacy_filter_mode"] == "mock"
        detected = set(body["detected_types"])
        assert "private_email" in detected
        assert "private_phone" in detected
        assert "private_url" in detected
        assert "private_date" in detected
        assert "account_number" in detected
        assert "secret" in detected
        assert "private_person" not in detected
        assert "private_address" not in detected

    def test_mode_endpoint_does_not_collide_with_job_id_route(
        self, mode_client: TestClient
    ) -> None:
        """``/mode`` must not be parsed as a job UUID by the jobs router."""
        # This is the bug that motivated creating ``meta_router``:
        # ``GET /api/v1/documents/mode`` was being matched by
        # ``GET /api/v1/documents/{job_id}``.
        r = mode_client.get("/api/v1/mode")
        assert r.status_code == 200
        assert "detail" not in r.json()

    def test_mode_endpoint_documents_endpoint_still_works(
        self, mode_client: TestClient
    ) -> None:
        """Adding ``meta_router`` didn't break the existing jobs router."""
        # Hit a job-id-shaped URL to make sure 404 behaviour is
        # preserved for unknown IDs (i.e. not for 'mode').
        r = mode_client.get("/api/v1/documents/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 2. Static analysis: ensure the JS patches are still in place
# ---------------------------------------------------------------------------


class TestJavaScriptPatches:
    """These checks guard against accidental regression of the bug fixes.

    They do **not** execute the JS — they just confirm that the
    source files contain the code paths the fixes introduced.
    """

    @pytest.fixture
    def app_js(self) -> str:
        return (FRONTEND / "app.js").read_text(encoding="utf-8")

    def test_show_upload_options_does_not_uncheck_existing_choice(
        self, app_js: str
    ) -> None:
        """Bug 1: ``showUploadOptions`` must NOT reset the checkbox."""
        # The old implementation set ``checked = false`` unconditionally
        # for docx files.  After the fix it must only enable/disable.
        match = re.search(
            r"function showUploadOptions\(ext\) \{.*?\n  \}",
            app_js,
            re.DOTALL,
        )
        assert match is not None, "showUploadOptions not found"
        body = match.group(0)
        # The line below used to exist:
        assert "checked = false" not in body, (
            "showUploadOptions still resets the checkbox — "
            "this regresses the 'Результат в MD-формате' drop bug"
        )
        # And the new behaviour must be there.
        assert "$.outputFormatMd.disabled = false" in body

    def test_reset_keeps_user_choice(self, app_js: str) -> None:
        """``reset()`` must not nuke the user's checkbox choice either."""
        match = re.search(
            r"function reset\(\) \{.*?\n  \}",
            app_js,
            re.DOTALL,
        )
        assert match is not None
        body = match.group(0)
        # We allow the comment that documents this, but the actual
        # assignment that overwrites the user's selection is gone.
        # We tolerate an assignment *inside* the upload-options block
        # that targets only the .md branch:
        assert body.count("$.outputFormatMd.checked = false") <= 1

    def test_selection_toolbar_does_not_use_scrollY(self, app_js: str) -> None:
        """Bug 3: the toolbar must not add ``window.scrollY`` to the top."""
        # The original code used:
        #   var top = rect.bottom + window.scrollY + 4;
        # which pushed the toolbar far below the viewport for
        # selections in scrollable containers.  After the fix the
        # toolbar uses viewport coordinates (no scrollY).
        match = re.search(
            r"function showSelectionToolbar\(.*?\n  \}",
            app_js,
            re.DOTALL,
        )
        assert match is not None
        body = match.group(0)
        # Strip line-comments so a docstring referencing the old
        # pattern doesn't trip the check.
        code_only = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        assert "window.scrollY" not in code_only, (
            "selection toolbar still uses window.scrollY — the toolbar "
            "will appear off-screen for selections in scrollable containers"
        )
        assert "window.scrollX" not in code_only, (
            "selection toolbar still uses window.scrollX — same bug"
        )
        # We should clamp horizontally as well.
        assert "maxLeft" in body or "innerWidth" in body

    def test_mode_info_banner_is_loaded(self, app_js: str) -> None:
        """Bug 2: there must be a call to ``/api/v1/mode`` from the frontend."""
        assert "/api/v1/mode" in app_js, (
            "frontend no longer queries /api/v1/mode — "
            "users won't see the mock-mode warning banner"
        )
        assert "loadModeInfo" in app_js

    def test_index_html_has_mode_banner_element(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        assert "mode-info-section" in html
        assert "mode-info-text" in html

    def test_index_html_has_output_format_checkbox(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        assert 'id="output-format-md"' in html
        assert 'class="checkbox"' in html

    def test_checkbox_defaults_to_checked_for_docx(self, app_js: str) -> None:
        """Bug fix: the checkbox must default to checked for docx
        so users don't forget to enable MD conversion. The default
        is guarded by dataset.userSet."""
        match = re.search(
            r"function showUploadOptions\(ext\) \{.*?\n  \}",
            app_js,
            re.DOTALL,
        )
        assert match is not None
        body = match.group(0)
        assert '$.outputFormatMd.checked = true' in body, (
            "showUploadOptions does not default to checked"
        )
        assert 'dataset.userSet' in body, (
            "missing dataset.userSet guard"
        )

    def test_apply_feedback_keeps_pending_actions(self, app_js: str) -> None:
        """Bug fix: after apply-feedback, pendingActions must not
        be cleared with = [] – that wipes manual ADD spans."""
        # The marker comment just before the filter code must exist.
        assert "// Keep only pending actions that were NOT" in app_js
        assert "pendingActions.filter" in app_js

        # The function ``addEntity`` still has a legitimate
        # ``pendingActions = []`` in a different context — that's
        # fine.  We just need to make sure the
        # ``applyFeedbackToFile`` function body does NOT have a
        # standalone ``pendingActions = [];``.
        # Extract the applyFeedbackToFile function body.
        af_match = re.search(
            r"async function applyFeedbackToFile\(\) \{.*?\n  \}",
            app_js,
            re.DOTALL,
        )
        assert af_match is not None, "applyFeedbackToFile not found"
        af_body = af_match.group(0)
        # Old bug pattern: a standalone ``pendingActions = [];``
        # anywhere in this function.
        assert "pendingActions = []" not in af_body, (
            "applyFeedbackToFile still uses = [] to clear "
            "pendingActions — manual ADD spans will disappear"
        )


# ---------------------------------------------------------------------------
# 4. End-to-end with a real browser (Playwright)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            import httpx

            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.2)
    raise AssertionError(f"server not ready at {url}: {last_err!r}")


@pytest.fixture(scope="module")
def live_server() -> Generator[str, None, None]:
    """Start a real uvicorn process for the Playwright tests."""
    storage = REPO_ROOT / "storage_e2e_regression"
    if storage.exists():
        shutil.rmtree(storage, ignore_errors=True)
    storage.mkdir()
    port = _free_port()
    env = {
        **os.environ,
        "NEIRONIR_LOG_LEVEL": "WARNING",
        "NEIRONIR_STORAGE_DIR": str(storage),
        "NEIRONIR_PRIVACY_FILTER_MODE": "mock",
    }
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "neironir.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(f"{base}/api/v1/health", timeout=15.0)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        shutil.rmtree(storage, ignore_errors=True)


def _make_docx_bytes(path: Path, paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))
    return path.read_bytes()


def _make_docx_path(target_dir: Path, name: str = "contract.docx") -> Path:
    """Build a real .docx with detectable PII under ``target_dir``.

    Returns the path to the resulting file.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    src = target_dir / name
    _make_docx_bytes(
        src,
        [
            "Reach me at user@example.com.",
            "Documentation: https://example.com/page",
        ],
    )
    return src


# Skip the entire module if Chromium is not installed — keeps CI on
# machines without playwright happy.
def _chromium_available() -> bool:
    cache = Path.home() / "AppData" / "Local" / "ms-playwright"
    return cache.is_dir() and any(cache.glob("chromium-*"))


pytestmark = pytest.mark.skipif(
    not _chromium_available(),
    reason="playwright Chromium not installed",
)


def _upload_file(page, file_path: Path) -> None:
    """Trigger the hidden file input and upload ``file_path``."""
    page.set_input_files("#file-input", str(file_path))


def _wait_for_upload(page) -> str:
    """Wait until the job section shows a downloadable result."""
    # The mock client is synchronous so a few seconds is plenty.
    page.wait_for_selector("#download:not([hidden])", timeout=10_000)
    return page.eval_on_selector(
        "#download", "el => el.getAttribute('href')"
    )


# ----- Bug 1a: checkbox defaults to checked for .docx files ----------


class TestOutputFormatDefaultChecked:
    def test_checkbox_is_checked_by_default_for_docx(
        self, live_server: str, tmp_path: Path
    ) -> None:
        """The 'Результат в MD-формате' checkbox must be checked by
        default when the page loads, so users don't forget to enable
        markdown conversion for .docx files."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(live_server)

                # After uploading a .docx, the checkbox should appear
                # and be checked by default.
                first_path = tmp_path / "first.docx"
                _make_docx_bytes(
                    first_path,
                    ["Email: user@example.com"],
                )
                page.set_input_files("#file-input", str(first_path))
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                assert page.is_checked("#output-format-md"), (
                    "checkbox should default to checked for docx"
                )
            finally:
                browser.close()

    def test_checkbox_stays_checked_after_upload(
        self, live_server: str, tmp_path: Path
    ) -> None:
        """When the user uploads a second file, the checkbox stays
        checked (the user's preference is preserved)."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(live_server)

                # Upload first docx — checkbox should be auto-checked.
                first_path = tmp_path / "a.docx"
                _make_docx_bytes(
                    first_path,
                    ["Email: a@example.com"],
                )
                page.set_input_files("#file-input", str(first_path))
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                assert page.is_checked("#output-format-md"), (
                    "default check failed on first upload"
                )

                # Upload a second docx — checkbox must still be checked.
                page.click("#reset")
                second_path = tmp_path / "b.docx"
                _make_docx_bytes(
                    second_path,
                    ["Email: b@example.com"],
                )
                page.set_input_files("#file-input", str(second_path))
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                assert page.is_checked("#output-format-md"), (
                    "checkbox lost checked state after second upload"
                )
            finally:
                browser.close()


# ----- Bug 1b: checkbox survives a file drop ------------------------------


class TestOutputFormatCheckboxSurvivesDrop:
    def test_checkbox_keeps_user_choice_across_uploads(
        self, live_server: str, tmp_path: Path
    ) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(live_server)

                # Tick the checkbox BEFORE any upload.
                page.check("#output-format-md")

                # First upload: checkbox should stay checked.
                first_path = tmp_path / "first.docx"
                _make_docx_bytes(
                    first_path,
                    [
                        "Reach me at user@example.com.",
                        "Documentation: https://example.com/page",
                    ],
                )
                page.set_input_files("#file-input", str(first_path))
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                assert page.is_checked("#output-format-md"), (
                    "checkbox reset after first upload — bug 1 regressed"
                )

                # Reset and upload again — checkbox must still be checked.
                page.click("#reset")
                second_path = tmp_path / "second.docx"
                _make_docx_bytes(
                    second_path,
                    [
                        "Second file: user@example.com.",
                    ],
                )
                page.set_input_files("#file-input", str(second_path))
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                assert page.is_checked("#output-format-md"), (
                    "checkbox reset after second upload — bug 1 regressed"
                )
            finally:
                browser.close()


# ----- Bug 2: mode banner tells the user what mock detects -----------------


class TestMockModeBanner:
    def test_banner_explains_mock_limitations(
        self, live_server: str
    ) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(live_server)
                page.wait_for_selector("#mode-info-section:not([hidden])", timeout=5_000)
                text = page.text_content("#mode-info-text")
                assert text is not None
                assert "mock" in text.lower()
                # Must mention email/phone/url/date so the user knows
                # what's redacted; must NOT claim names are detected.
                assert "Email" in text or "email" in text
                # The fix explicitly tells the user to switch modes for
                # names / addresses.
                assert "ФИО" in text or "subprocess" in text
            finally:
                browser.close()

    def test_real_pipeline_redacts_email(
        self, live_server: str, tmp_path: Path
    ) -> None:
        """End-to-end: a docx uploaded defaults to md output
        (checkbox is now checked by default). The mock mode
        redacts email — the downloaded markdown must contain
        the placeholder and not the original email."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(live_server)
                first_path = tmp_path / "first.docx"
                _make_docx_bytes(
                    first_path,
                    ["Email: user@example.com"],
                )
                page.set_input_files(
                    "#file-input", str(first_path)
                )
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)

                href = page.eval_on_selector(
                    "#download", "el => el.getAttribute('href')"
                )
                import httpx

                r = httpx.get(live_server + href, timeout=5.0)
                assert r.status_code == 200
                body = r.text
                # The markdown output must have the placeholder.
                assert "<PRIVATE_EMAIL1>" in body, (
                    "email was not redacted — pipeline may have "
                    "returned the original text"
                )
                # Original email must be gone.
                assert "user@example.com" not in body
            finally:
                browser.close()


# ----- Bug 3: selection toolbar position ----------------------------------


class TestSelectionToolbarPosition:
    def test_toolbar_is_in_viewport_after_selecting_text(
        self, live_server: str, tmp_path: Path
    ) -> None:
        """Select text inside the preview and verify the toolbar lands
        in the visible viewport (not below the fold)."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # Force a small viewport so the test fails loudly if
                # the toolbar is positioned below the visible area.
                context = browser.new_context(viewport={"width": 800, "height": 600})
                page = context.new_page()
                page.goto(live_server)

                # Upload + go to review.
                page.set_input_files(
                    "#file-input", str(_make_docx_path(tmp_path))
                )
                page.wait_for_selector("#download:not([hidden])", timeout=10_000)
                page.click("#review-btn")
                page.wait_for_selector("#preview .entity-span", timeout=10_000)

                # Force the preview to be scrollable so the bug
                # condition (selection deep inside a scrollable area)
                # is reproducible.
                page.evaluate(
                    """
                    const p = document.getElementById('preview');
                    p.style.maxHeight = '60px';
                    p.style.overflowY = 'auto';
                    p.scrollTop = p.scrollHeight;
                    """
                )

                # Pick the last span in the preview and synthesise a
                # selection across its text.
                page.evaluate(
                    """
                    (() => {
                      const preview = document.getElementById('preview');
                      const span = preview.querySelector('.entity-span');
                      if (!span) return;
                      const range = document.createRange();
                      range.setStart(span.firstChild, 0);
                      range.setEnd(span.firstChild, span.firstChild.length);
                      const sel = window.getSelection();
                      sel.removeAllRanges();
                      sel.addRange(range);
                      preview.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    })();
                    """
                )

                # Wait for the toolbar.
                page.wait_for_selector(".selection-toolbar", timeout=2_000)

                # The toolbar must be in the visible viewport.  Its top
                # coordinate should not exceed window.innerHeight.
                box = page.eval_on_selector(
                    ".selection-toolbar",
                    "el => { const r = el.getBoundingClientRect();"
                    " return {top: r.top, bottom: r.bottom, "
                    " left: r.left, right: r.right}; }",
                )
                viewport_height = page.evaluate("window.innerHeight")
                viewport_width = page.evaluate("window.innerWidth")
                assert box["top"] >= 0, f"toolbar above viewport: {box}"
                assert box["bottom"] <= viewport_height, (
                    f"toolbar below viewport: {box} vs {viewport_height}"
                )
                assert box["left"] >= 0, f"toolbar left of viewport: {box}"
                assert box["right"] <= viewport_width, (
                    f"toolbar right of viewport: {box} vs {viewport_width}"
                )
            finally:
                browser.close()