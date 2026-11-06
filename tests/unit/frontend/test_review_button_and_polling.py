"""Static regression for T12 frontend fixes (#24–#27).

- #24: the "Сохранить правки" (review) button is shown only for ``md`` output;
  apply-feedback rejects DOCX with 400, so offering it for DOCX was a dead end.
- #25: ``typeToPrefix`` must include ``private_organization`` — without it an
  added organization placeholder collided with the UNKNOWN fallback regex and
  numbering restarted at 1 on every add.
- #26: the selection toolbar must offer ``private_url`` like every other type.
- #27: admin.js training polling must stop on a final status and on unload.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


def _app_source() -> str:
    return (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")


def _admin_source() -> str:
    return (FRONTEND_DIR / "admin.js").read_text(encoding="utf-8")


def test_review_button_only_for_md_output() -> None:
    src = _app_source()
    assert 'ext !== "md"' in src, "review button visibility not tied to md output"
    # The old unconditional show must be gone from the completed branch.
    completed_block = src[src.index('if (job.status === "completed")') :]
    block = completed_block[: completed_block.index("function")]
    assert re.search(r"\$\.reviewBtn\.hidden = ext !== \"md\";", block), (
        "expected conditional reviewBtn.hidden assignment"
    )


def test_type_to_prefix_includes_private_organization() -> None:
    src = _app_source()
    assert 'private_organization: "PRIVATE_ORGANIZATION"' in src


def test_selection_toolbar_offers_private_url() -> None:
    src = _app_source()
    toolbar_block = src[src.index("var entityTypes = [") :]
    toolbar_block = toolbar_block[: toolbar_block.index("];")]
    assert '"private_url"' in toolbar_block, "URL button missing from selection toolbar"


def test_admin_training_poll_stops_on_final_status() -> None:
    src = _admin_source()
    assert "stopTrainingPolling()" in src
    poll_block = src[src.index("function pollTrainingStatus") :]
    poll_block = poll_block[: poll_block.index("function stopTrainingPolling")]
    assert 'body.status !== "running"' in poll_block
    assert "stopTrainingPolling();" in poll_block


def test_admin_clear_interval_defined_and_on_unload() -> None:
    src = _admin_source()
    assert re.search(r"function stopTrainingPolling\(\)[\s\S]*clearInterval\(trainingPollTimer\)", src)
    assert 'window.addEventListener("beforeunload", stopTrainingPolling)' in src
