"""Conservative ReDoS guard for dynamic regex rules.

Implements spec 007 (TD-004) "conservative regex policy": bounded
pattern length plus rejection of known catastrophic-backtracking
constructs (nested quantifiers, backreferences) at rule admission and
at persisted-rule load time. No external dependency is used; the check
is a deterministic, conservative syntactic filter over the pattern
source string.

The policy is intentionally stricter than necessary — some legitimate
advanced regular expressions are rejected (documented trade-off in the
spec). The validator never sees or returns document text; it only ever
inspects the pattern source and returns a stable machine-readable
reason plus a short detail string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "DEFAULT_DYNAMIC_RULE_MAX_LENGTH",
    "DynamicPatternPolicy",
    "DynamicRuleRejection",
    "REASON_BACKREFERENCE",
    "REASON_NESTED_QUANTIFIER",
    "REASON_PATTERN_TOO_LONG",
    "UNSAFE_REJECTION",
    "validate_dynamic_pattern",
]

# ---------------------------------------------------------------------------
# Stable rejection reasons
# ---------------------------------------------------------------------------

REASON_PATTERN_TOO_LONG: Literal["pattern_too_long"] = "pattern_too_long"
REASON_NESTED_QUANTIFIER: Literal["nested_quantifier"] = "nested_quantifier"
REASON_BACKREFERENCE: Literal["backreference"] = "backreference"

#: Stable error code used by ``POST /api/v1/rules`` for any rejection
#: produced by :func:`validate_dynamic_pattern`.
UNSAFE_REJECTION: Final = "unsafe_regex"


@dataclass(frozen=True, slots=True)
class DynamicRuleRejection:
    """Structured rejection returned by :func:`validate_dynamic_pattern`.

    Attributes:
        reason: Stable machine-readable code — one of
            ``pattern_too_long``, ``nested_quantifier``, ``backreference``.
        detail: Short human-readable explanation. Never contains
            document text; only the offending pattern fragment.
        offset: Character offset in the pattern where the offending
            construct was found (``-1`` for length violations).
    """

    reason: str
    detail: str
    offset: int = -1


#: Maximum length for a dynamic rule pattern source.
#:
#: The spec design names ``dynamic_rule_max_length`` (default 200) as a
#: settings field, but adding a new config field is out of T5 scope —
#: the value is therefore fixed here as a module constant. Every
#: built-in static pattern in ``neironir.privacy.rules`` is well below
#: this bound, so the default cannot break the built-in rule set.
DEFAULT_DYNAMIC_RULE_MAX_LENGTH: Final = 200


# ---------------------------------------------------------------------------
# Internal scanning helpers
# ---------------------------------------------------------------------------

# Quantifier tokens; the ``{`` token is only a quantifier when it forms
# a valid ``{m}`` / ``{m,}`` / ``{m,n}`` group (Python ``re`` treats a
# bare ``{`` as a literal).
_QUANTIFIER_CHARS = frozenset("{?*+")


def _quantifier_span(pattern: str, i: int) -> int | None:
    """Return the index *after* the quantifier starting at ``pattern[i]``,
    or ``None`` if ``pattern[i]`` is not a quantifier.
    """
    ch = pattern[i]
    if ch in "?*+":
        return i + 1
    if ch != "{":
        return None

    j = i + 1
    m_digits = 0
    while j < len(pattern) and pattern[j].isdigit():
        m_digits += 1
        j += 1
    if m_digits == 0:
        return None
    if j < len(pattern) and pattern[j] == "}":
        return j + 1
    if j < len(pattern) and pattern[j] == ",":
        k = j + 1
        n_digits = 0
        while k < len(pattern) and pattern[k].isdigit():
            n_digits += 1
            k += 1
        if k < len(pattern) and pattern[k] == "}":
            return k + 1
    return None


def _scan_nested_quantifier(pattern: str) -> int:
    """Conservative scanner for nested quantifiers such as ``(a+)+``.

    Tracks per open scope (``(`` or ``[``) whether a quantifier inside
    the scope is still "active" — i.e. has not yet been consumed by a
    closer or a following atom. When the scope closes and the closer is
    itself immediately quantified, the inner quantifier is quantified
    by the outer one → catastrophic backtracking risk.

    Returns:
        Offset of the *outer* quantifier if a nesting is detected,
        otherwise ``-1``.

    The algorithm is intentionally conservative (may accept some exotic
    unsafe forms, but never rejects a plain quantifier like ``a+`` or
    ``\\d{3}``).
    """
    # Stack entries: [inner_quant_active: bool, inner_quant_offset: int]
    scopes: list[list[object]] = []

    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]

        if ch == "\\" and i + 1 < n:
            # Escaped character: single atom, no scope change.
            i += 2
            continue

        if ch == "[":
            scopes.append([False, -1])
            i += 1
            continue

        if ch == "(":
            scopes.append([False, -1])
            i += 1
            continue

        if ch in "])":
            if scopes:
                scope = scopes.pop()
                inner_active = scope[0] is True
                j = i + 1
                end = _quantifier_span(pattern, j) if j < n else None
                if end is not None and inner_active:
                    return j
                # The closer also consumes the inner quantifier if it
                # was active — no outer quantifier to nest under.
                del inner_active, end
            i += 1
            continue

        if ch in _QUANTIFIER_CHARS:
            end = _quantifier_span(pattern, i)
            if end is not None:
                # Mark innermost open scope as having an active inner
                # quantifier; if there is no open scope, the quantifier
                # quantifies the preceding atom — safe.
                if scopes:
                    scopes[-1][0] = True
                    if scopes[-1][1] == -1:
                        scopes[-1][1] = i
                i = end
                continue
            i += 1
            continue

        # Plain atom: nothing to track.
        i += 1

    return -1


def _find_backreference(pattern: str) -> int:
    """Return the offset of a backreference or ``-1``.

    Recognised forms: ``\\1`` .. ``\\9`` (decimal backreferences) and
    ``\\g<name>`` / ``\\g<number>`` (including ``\\g<1>``). ``\\b``,
    ``\\d`` etc. are not backreferences and are not flagged.
    """
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] != "\\":
            i += 1
            continue
        if i + 1 >= n:
            return -1  # trailing backslash → re.error at compile time
        nxt = pattern[i + 1]
        if nxt.isdigit() and nxt != "0":
            return i
        if nxt == "g" and i + 2 < n and pattern[i + 2] == "<":
            return i
        i += 2
    return -1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_dynamic_pattern(
    pattern: str,
    *,
    max_length: int = DEFAULT_DYNAMIC_RULE_MAX_LENGTH,
) -> DynamicRuleRejection | None:
    """Validate a dynamic rule pattern against the conservative policy.

    Args:
        pattern: Regex pattern source (Python ``re`` dialect).
        max_length: Maximum allowed pattern length. Defaults to the
            module constant :data:`DEFAULT_DYNAMIC_RULE_MAX_LENGTH`
            (200), the value mandated by spec design TD-004.

    Returns:
        ``None`` if the pattern passes the policy, or a
        :class:`DynamicRuleRejection` describing the first violation.
        Checks run in order: length, nested quantifier, backreference.
    """
    if len(pattern) > max_length:
        return DynamicRuleRejection(
            reason=REASON_PATTERN_TOO_LONG,
            detail=f"pattern length {len(pattern)} exceeds limit {max_length}",
            offset=-1,
        )

    offset = _scan_nested_quantifier(pattern)
    if offset != -1:
        snippet = pattern[max(0, offset - 12) : offset + 12]
        return DynamicRuleRejection(
            reason=REASON_NESTED_QUANTIFIER,
            detail=f"nested quantifier near offset {offset} in {snippet!r}",
            offset=offset,
        )

    offset = _find_backreference(pattern)
    if offset != -1:
        snippet = pattern[offset : offset + 8]
        return DynamicRuleRejection(
            reason=REASON_BACKREFERENCE,
            detail=f"backreference {snippet!r} at offset {offset}",
            offset=offset,
        )

    return None


class DynamicPatternPolicy:
    """Class-based wrapper around :func:`validate_dynamic_pattern`.

    Exposed as a class so that tests (or future configuration hooks)
    can substitute a permissive policy via subclassing without touching
    the module-level defaults.
    """

    max_length: int = DEFAULT_DYNAMIC_RULE_MAX_LENGTH

    @classmethod
    def validate(cls, pattern: str) -> DynamicRuleRejection | None:
        return validate_dynamic_pattern(pattern, max_length=cls.max_length)
