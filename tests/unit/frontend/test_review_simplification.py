"""Static-analysis regression tests for the simplified review UI.

These tests read the shipped ``frontend/index.html`` and ``frontend/app.js``
sources and assert structural invariants:

- the three useless buttons (``#confirm-all``, ``#submit-feedback``,
  ``#skip-review``) and their associated DOM/JS were removed;
- the comment section and the "feedback saved" message are gone;
- the ``postFeedback`` function was removed;
- the ``#apply-feedback`` button remains and is disabled when there is
  nothing to apply;
- after a successful apply-feedback call the preview is re-rendered from a
  locally updated ``reviewData`` (not from a stale ``GET /annotations``).

See `.ai/sdd/specs/001-apply-feedback-to-result/` (FR-003, FR-005, T3, T5).
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"

INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "app.js"

REMOVED_IDS = [
    "confirm-all",
    "submit-feedback",
    "skip-review",
    "feedback-success",
    "comment-section",
]


def _index_source() -> str:
    assert INDEX_HTML.exists(), f"missing {INDEX_HTML}"
    return INDEX_HTML.read_text(encoding="utf-8")


def _app_source() -> str:
    assert APP_JS.exists(), f"missing {APP_JS}"
    return APP_JS.read_text(encoding="utf-8")


def test_index_has_no_removed_buttons() -> None:
    src = _index_source()
    for elem_id in REMOVED_IDS:
        assert f'id="{elem_id}"' not in src, f"{elem_id} still present in index.html"


def test_index_still_has_apply_button() -> None:
    src = _index_source()
    assert 'id="apply-feedback"' in src
    assert "Сохранить правки в файл" in src


def test_index_apply_button_disabled_by_default() -> None:
    src = _index_source()
    # The button must be disabled until there is something to apply.
    m = re.search(r'<button[^>]*id="apply-feedback"[^>]*>', src)
    assert m, "apply-feedback button not found"
    assert 'disabled' in m.group(0), "apply-feedback button should be disabled by default"


def test_app_has_no_removed_handlers() -> None:
    src = _app_source()
    for func in ["confirmAll", "submitFeedback", "postFeedback"]:
        assert f"function {func}(" not in src, f"{func}() still defined in app.js"
        assert f"{func}(" not in src, f"call to {func}() still present in app.js"


def test_app_has_no_removed_dom_refs() -> None:
    src = _app_source()
    for ref in [
        "$.confirmAll",
        "$.submitFeedback",
        "$.skipReview",
        "$.feedbackSuccess",
        "$.commentSection",
        "$.feedbackComment",
    ]:
        assert ref not in src, f"{ref} still referenced in app.js"


def test_app_has_update_apply_button() -> None:
    src = _app_source()
    assert "function updateApplyButton()" in src
    assert "updateApplyButton();" in src
    # It must be called from openReview, add, reject and after apply.
    assert (
        src.count("updateApplyButton();") >= 3
    ), "updateApplyButton() not wired enough"


def test_app_apply_feedback_updates_preview_locally() -> None:
    """After a successful apply-feedback POST, the preview is re-rendered from
    locally modified reviewData (FR-001), not from a stale GET /annotations."""
    src = _app_source()
    # The function must no longer re-fetch annotations after success.
    assert (
        "reviewData = await ann.json()" not in src
    ), "stale GET /annotations re-fetch still present"
    # It must modify reviewData (reject/add splices) and then re-render.
    assert (
        "reviewData.spans = keptSpans" in src
    ), "rejected spans are not dropped from reviewData"
    assert (
        "reviewData.text = newText" in src
    ), "reviewData.text is not updated locally"
    # renderPreview must be called after the local update, within
    # applyFeedbackToFile. Locate the applyFeedbackToFile body between its
    # function header and the final catch/finally.
    fn_start = src.find("async function applyFeedbackToFile()")
    assert fn_start != -1
    fn_end = src.find("function showApplyError(", fn_start)
    assert fn_end != -1
    body = src[fn_start:fn_end]
    text_idx = body.find("reviewData.text = newText")
    assert text_idx != -1, "reviewData.text is not updated in applyFeedbackToFile"
    render_idx = body.find("renderPreview();", text_idx)
    assert render_idx != -1, "renderPreview() must be called after the local reviewData update"
    # No stale re-fetch of /annotations inside applyFeedbackToFile.
    # NB: "apply-feedback" contains the substring "/annotations", so match
    # the actual fetch URL pattern used by the old buggy code.
    assert '/api/v1/documents/" + currentJobId + "/annotations"' not in body, (
        "applyFeedbackToFile still re-fetches stale annotations"
    )


def test_app_has_next_placeholder_helper() -> None:
    src = _app_source()
    assert "function nextPlaceholderForType(" in src
    assert "PRIVATE_PERSON" in src
    assert "SECRET" in src


def test_app_next_placeholder_handles_batch_adds() -> None:
    """Two add-actions of the same type in one apply batch must get distinct
    placeholders (review.md Issue 1). The batch counter must be seeded from
    the placeholder just issued."""
    src = _app_source()
    # nextPlaceholderForType must accept extraTaken numbers.
    fn = src[src.find("function nextPlaceholderForType(") :]
    fn = fn[: fn.find("\n  }")]
    assert "extraTaken" in fn, "nextPlaceholderForType must accept extraTaken"
    assert "(extraTaken || []).forEach" in fn, "extraTaken not applied"
    # applyFeedbackToFile must maintain a batch counter per entity_type.
    body_start = src.find("async function applyFeedbackToFile()")
    body = src[body_start: src.find("function showApplyError(", body_start)]
    assert "batchTaken" in body, "batch counter missing in applyFeedbackToFile"
    assert "replacement.match" in body, "batch counter not seeded from placeholder"


def test_app_no_stale_annotations_refetch() -> None:
    src = _app_source()
    # The old buggy pattern re-fetched /annotations inside applyFeedbackToFile.
    assert "apply-feedback" in src  # the endpoint is still used
    # Ensure the only fetch to annotations is in openReview, not in apply flow.
    # T11: the fetch uses a captured myJobId snapshot instead of the live
    # currentJobId, so a late response cannot target a newer job.
    ann_fetches = re.findall(
        r'fetch\("/api/v1/documents/" \+ (?:currentJobId|myJobId) \+ "/annotations"[^;]*', src
    )
    assert len(ann_fetches) == 1, (
        f"expected exactly 1 annotations fetch (openReview), got {len(ann_fetches)}"
    )


def test_upload_race_guard_present() -> None:
    """T10: a stale upload response must not overwrite the UI state.

    ``upload()`` takes a monotonic sequence token before the fetch and
    checks it after every await; responses of superseded uploads return
    early without touching ``currentJobId``.
    """
    src = _app_source()
    assert "var myUpload = ++uploadSeq;" in src, "no upload sequence token"
    # Every path that mutates shared state after an await is guarded.
    guarded = [
        line.strip()
        for line in src.splitlines()
        if "myUpload !== uploadSeq" in line
    ]
    assert len(guarded) >= 3, f"expected guards on all post-await paths, got {guarded}"
    # currentJobId assignment happens only under the guard.
    assign_idx = src.index("currentJobId = job.id")
    guard_idx = src.rindex("myUpload !== uploadSeq", 0, assign_idx)
    assert guard_idx > src.index("var myUpload = ++uploadSeq")


def test_render_preview_normalizes_malformed_spans() -> None:
    """T6: renderPreview must clamp/drop negative, out-of-range, reversed and
    overlapping spans before rendering.  Malformed spans must never throw or
    inject HTML: the function keeps using text nodes / textContent only, and
    the filter runs on the sorted span list right before the render loop."""
    src = _app_source()
    fn_start = src.index("function renderPreview()")
    fn_end = src.index("function toggleReject(", fn_start)
    body = src[fn_start:fn_end]
    # The filter must exist and guard the documented malformed cases.
    assert "Number.isInteger" in body, "offsets are not validated as integers"
    assert "text.length" in body, "spans are not clamped against text length"
    # Out-of-range (fully before/after the text) and reversed spans are dropped.
    assert "e < 0" in body, "negative end not handled"
    assert "e < s" in body, "reversed spans are not dropped"
    # Overlap guard: a span starting inside the previously kept span is dropped.
    assert "cursor" in body, "no overlap cursor in renderPreview"
    assert "s < cursor" in body, "overlapping spans are not dropped"
    # The render loop must iterate the filtered list, not the raw one.
    assert "validSpans.forEach" in body, "render loop does not use the filtered span list"
    # No HTML injection: innerHTML must never be assigned inside renderPreview.
    assert "innerHTML" not in body, "renderPreview must not assign innerHTML"
    # textContent is still the only element-content assignment (escape-safe).
    assert "textContent" in body


def test_open_review_race_guard() -> None:
    """T11: a late annotations response must not overwrite a newer review.

    ``openReview`` captures a monotonic ``reviewSeq`` token and the job id;
    after every await it verifies both still match, otherwise it returns
    without touching ``reviewData``.  ``closeReview`` bumps the token so an
    abandoned session is invalidated too.
    """
    src = _app_source()
    assert "var myReview = ++reviewSeq;" in src, "no review session token"
    # Guards on all post-await paths of openReview.
    fn = src[src.index("async function openReview") : src.index("function closeReview")]
    guarded = [line for line in fn.splitlines() if "myReview !== reviewSeq" in line]
    assert len(guarded) >= 3, f"expected guards on all post-await paths, got {len(guarded)}"
    # reviewData assignment only under the guard.
    assign_idx = fn.index("reviewData = data")
    guard_idx = fn.rindex("myReview !== reviewSeq", 0, assign_idx)
    assert guard_idx < assign_idx
    # closeReview invalidates in-flight loads.
    close_fn = src[src.index("function closeReview") : src.index("function renderPreview")]
    assert "reviewSeq++" in close_fn
