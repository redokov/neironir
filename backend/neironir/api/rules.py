"""HTTP endpoints for managing detection rules generated from feedback.

The lifecycle of a rule:

1. :class:`~neironir.privacy.feedback_analyzer.FeedbackAnalyzer` scans
   accumulated user corrections and emits **proposed** rules.
2. An admin reviews proposals via ``GET /api/v1/rules`` and approves or
   rejects them.
3. Approved rules are compiled and injected into the
   :class:`~neironir.privacy.rules.RuleBasedDetector` at runtime.
4. Manually-written rules can also be added via ``POST /api/v1/rules``.

Endpoints
---------

* ``GET  /api/v1/rules`` — list all rules (built-in + proposed + approved + rejected).
* ``GET  /api/v1/rules/stats`` — aggregate feedback statistics.
* ``POST /api/v1/rules/proposals`` — trigger analysis and return new proposals.
* ``POST /api/v1/rules/{rule_id}/approve`` — approve a proposed rule.
* ``POST /api/v1/rules/{rule_id}/reject`` — reject a proposed rule.
* ``POST /api/v1/rules`` — manually add a custom rule.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from neironir.api.dependencies import get_settings
from neironir.config import Settings
from neironir.privacy.feedback_analyzer import FeedbackAnalyzer, ProposedRule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


# ---------------------------------------------------------------------------
# Storage helpers for rules metadata
# ---------------------------------------------------------------------------

_RULES_DIR_NAME = "rules"


def _rules_dir(settings: Settings) -> Path:
    """Return the path to the persistent rules storage directory."""
    path = Path(settings.storage_dir) / _RULES_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_rules_meta(storage_dir: Path) -> dict[str, dict]:
    """Load all rule metadata files from the rules directory.

    Returns a dict mapping ``rule_id`` → rule metadata dict.
    """
    rules: dict[str, dict] = {}
    for fpath in sorted(storage_dir.glob("rule_*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            rules[data.get("rule_id", fpath.stem)] = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("skipping malformed rule file %s: %s", fpath, exc)
    return rules


def _save_rule_meta(storage_dir: Path, rule: ProposedRule) -> str:
    """Persist a single rule metadata file. Returns the rule_id."""
    if not rule.rule_id:
        rule.rule_id = str(uuid4())
    fpath = storage_dir / f"rule_{rule.rule_id}.json"
    fpath.write_text(
        json.dumps(
            {
                "rule_id": rule.rule_id,
                "entity_type": rule.entity_type,
                "pattern": rule.pattern,
                "evidence_count": rule.evidence_count,
                "confidence": rule.confidence,
                "status": rule.status,
                "description": rule.description,
                "samples": rule.samples,
                "first_seen": rule.first_seen or datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return rule.rule_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_rules(
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Return all rules: built-in, proposed, approved, rejected."""
    storage_dir = _rules_dir(settings)
    rules = _load_rules_meta(storage_dir)
    return list(rules.values())


@router.get("/stats")
async def get_stats(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return aggregated feedback statistics across all jobs."""
    analyzer = FeedbackAnalyzer(storage_dir=Path(settings.storage_dir))
    stats = analyzer.compute_stats()
    return {
        "total_jobs_with_feedback": stats.total_jobs_with_feedback,
        "total_corrections": stats.total_corrections,
        "missed_types": dict(stats.missed_types),
        "false_positives": dict(stats.false_positives),
        "corrections_by_type": dict(stats.corrections_by_type),
    }


@router.post("/proposals")
async def generate_proposals(
    min_occurrences: int = 3,
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Run feedback analysis and return new proposed rules.

    Each proposal includes a regex pattern, entity type, evidence count,
    and confidence score. Proposals are **not** auto-approved; an admin
    must call ``POST /api/v1/rules/{id}/approve`` to activate a rule.

    Args:
        min_occurrences: Minimum ADD actions with the same pattern to
            generate a proposal (default 3).
    """
    analyzer = FeedbackAnalyzer(storage_dir=Path(settings.storage_dir))
    proposals = analyzer.propose_rules(min_occurrences=min_occurrences)

    # Save each proposal to persistent storage.
    storage_dir = _rules_dir(settings)
    results = []
    for proposal in proposals:
        _save_rule_meta(storage_dir, proposal)
        results.append({
            "rule_id": proposal.rule_id,
            "entity_type": proposal.entity_type,
            "pattern": proposal.pattern,
            "evidence_count": proposal.evidence_count,
            "confidence": proposal.confidence,
            "status": proposal.status,
            "description": proposal.description,
            "samples": proposal.samples,
        })

    logger.info("generated %d rule proposals", len(results))
    return results


@router.post("/{rule_id}/approve")
async def approve_rule(
    rule_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Approve a proposed rule and make it active.

    An approved rule will be compiled and injected into the
    ``RuleBasedDetector`` on the next warm-reload.
    """
    storage_dir = _rules_dir(settings)
    rules = _load_rules_meta(storage_dir)

    rule = rules.get(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule {rule_id} not found",
        )

    if rule.get("status") != "proposed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Rule {rule_id} has status '{rule.get('status')}'; expected 'proposed'",
        )

    rule["status"] = "approved"
    fpath = storage_dir / f"rule_{rule_id}.json"
    fpath.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("rule %s approved: %s (%s)", rule_id, rule.get("entity_type"), rule.get("pattern"))
    return {"status": "approved", "rule_id": rule_id}


@router.post("/{rule_id}/reject")
async def reject_rule(
    rule_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Reject a proposed rule (it will not be used for detection)."""
    storage_dir = _rules_dir(settings)
    rules = _load_rules_meta(storage_dir)

    rule = rules.get(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule {rule_id} not found",
        )

    rule["status"] = "rejected"
    fpath = storage_dir / f"rule_{rule_id}.json"
    fpath.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("rule %s rejected", rule_id)
    return {"status": "rejected", "rule_id": rule_id}


@router.post("")
async def add_manual_rule(
    entity_type: str,
    pattern: str,
    description: str = "",
    settings: Settings = Depends(get_settings),
) -> dict:
    """Manually add a custom regex rule.

    The rule is immediately saved with status ``approved`` so it will
    be picked up on the next warm-reload.

    Args:
        entity_type: One of the ``EntityType`` values (e.g. ``private_person``).
        pattern: A valid Python regex pattern string.
        description: Optional human-readable description.
    """
    if not pattern or len(pattern) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pattern must be at least 3 characters",
        )

    try:
        re.compile(pattern)
    except re.error as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid regex pattern: {exc}",
        )

    proposal = ProposedRule(
        entity_type=entity_type,
        pattern=pattern,
        evidence_count=0,
        confidence=0.9,
        status="approved",
        description=description or f"Manual rule for {entity_type}",
    )

    storage_dir = _rules_dir(settings)
    rule_id = _save_rule_meta(storage_dir, proposal)

    logger.info("manual rule added: %s — %s (%s)", rule_id, entity_type, pattern)
    return {
        "rule_id": rule_id,
        "entity_type": entity_type,
        "pattern": pattern,
        "status": "approved",
    }


__all__ = ["router"]
