"""Rule-based PII detector for Russian-language documents.

The :class:`RuleBasedDetector` complements the neural privacy-filter model
with explicit regex patterns and dictionary lookups for entity types that
the model was not trained on (e.g. Russian tax identifiers, bank account
prefixes, organisation names).

The patterns in this module are designed to catch the gaps observed in
real-world contract anonymisation. See ``docs/architecture.md`` for the
motivation and the ``Договоры/ф2.docx`` audit report.

Architecture
------------

The detector is **stateless and synchronous** — it takes a plain text
string and returns a list of :class:`~neironir.privacy.client.EntitySpan`
instances. It is invoked by :class:`CombinedPrivacyClient` **after** the
neural model, and its findings are merged with model output (model spans
win on overlap, rule spans fill gaps).

Overlap resolution
------------------

When a rule span overlaps with a higher-priority span (from the neural
model or from an earlier rule in the priority list), the shorter or
lower-priority span is dropped. The priority order is:

1. Neural model spans (always win)
2. Email / URL / Phone (precise patterns, low false-positive)
3. Bank account prefixes (р/с, к/с + 20 digits)
4. Tax identifiers (ИНН, КПП, ОГРН)
5. Person names with initials
6. Organisation names
7. Russian addresses
8. Russian text dates
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import ClassVar

from neironir.domain.entity_type import EntityType
from neironir.privacy.client import EntitySpan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class RuleBasedDetector:
    """Detect Russian PII entities using regex rules and dictionaries.

    Usage::

        detector = RuleBasedDetector()
        spans = detector.detect("ООО «Моторинвест», ИНН 7743776572")
    """

    # Maps entity type → list of (compiled regex, description) pairs.
    # Order within this list determines rule priority — earlier rules
    # win over later rules on overlap.
    RULES: ClassVar[list[tuple[EntityType, re.Pattern[str], str]]] = []

    # Lock protecting ``_DYNAMIC_RULES`` against concurrent read/write
    # from async endpoints (e.g. ``approve_rule`` in ``api/rules.py``
    # may race with ``detect()`` running in another coroutine).
    _dynamic_rules_lock: ClassVar[threading.Lock] = threading.Lock()

    # Compiled on import — see module-level ``_compile_rules()``.
    @classmethod
    def _init_rules(cls) -> None:
        if cls.RULES:
            return
        patterns: list[tuple[EntityType, re.Pattern[str], str]] = []

        # Priority order is CRITICAL — rules earlier in this list win over
        # later ones on overlap. The order below ensures:
        #   1. Email (precise, low FP)
        #   2. Tax IDs (ИНН/ОГРН with explicit prefix — before phone, because
        #      bare digit runs look like phone numbers)
        #   3. Bank accounts with prefix (р/с, к/с, БИК)
        #   4. Phone (generic, catches what\'s left)
        #   5. Contract numbers
        #   6. Person names / Organisation names / Addresses / Dates

        # -- 1. EMAIL --------------------------------------------------------
        patterns.append((EntityType.PRIVATE_EMAIL, re.compile(_EMAIL_PATTERN), "email"))

        # -- 2. TAX IDENTIFIERS (BEFORE PHONE! numeric IDs match phone too) --
        patterns.append((EntityType.ACCOUNT_NUMBER, re.compile(_INN_KPP_PATTERN), "inn_kpp"))
        patterns.append((EntityType.ACCOUNT_NUMBER, re.compile(_KPP_PATTERN), "kpp"))
        patterns.append((EntityType.ACCOUNT_NUMBER, re.compile(_OGRN_PATTERN), "ogrn"))

        # -- 3. BANK ACCOUNTS WITH EXPLICIT PREFIX ---------------------------
        patterns.append(
            (EntityType.ACCOUNT_NUMBER, re.compile(_BANK_ACCOUNT_PATTERN), "bank_account")
        )
        patterns.append(
            (EntityType.ACCOUNT_NUMBER, re.compile(_CORR_ACCOUNT_PATTERN), "corr_account")
        )
        patterns.append((EntityType.ACCOUNT_NUMBER, re.compile(_BIK_PATTERN), "bik"))

        # -- 4. PHONE (generic; catches any remaining digit runs) -------------
        patterns.append((EntityType.PRIVATE_PHONE, re.compile(_PHONE_PATTERN), "phone"))

        # -- 5. CONTRACT / DOCUMENT NUMBERS ----------------------------------
        patterns.append(
            (EntityType.ACCOUNT_NUMBER, re.compile(_CONTRACT_NUM_PATTERN), "contract_number")
        )

        # -- 6. PERSON NAMES WITH INITIALS -----------------------------------
        patterns.append(
            (EntityType.PRIVATE_PERSON, re.compile(_SURNAME_INITIALS_PATTERN), "surname_initials")
        )
        patterns.append(
            (EntityType.PRIVATE_PERSON, re.compile(_INITIALS_SURNAME_PATTERN), "initials_surname")
        )
        patterns.append(
            (EntityType.PRIVATE_PERSON, re.compile(_FULL_NAME_PATTERN), "full_name_triple")
        )

        # -- 7. ORGANISATION NAMES -------------------------------------------
        patterns.append(
            (EntityType.PRIVATE_PERSON, re.compile(_ORG_WITH_QUOTES_PATTERN), "org_quoted")
        )
        patterns.append(
            (EntityType.PRIVATE_PERSON, re.compile(_ORG_WITH_BRACKETS_PATTERN), "org_bracketed")
        )

        # -- 8. RUSSIAN ADDRESSES --------------------------------------------
        patterns.append(
            (EntityType.PRIVATE_ADDRESS, re.compile(_ADDRESS_PATTERN), "russian_address")
        )

        # -- 9. RUSSIAN TEXT DATES ------------------------------------------
        patterns.append(
            (EntityType.PRIVATE_DATE, re.compile(_RU_DATE_TEXT_PATTERN), "ru_date_text")
        )

        cls.RULES = patterns

    def __init__(self) -> None:
        """Initialise the rule-based detector."""
        self._known_organisations: list[str] = []
        # Lazy-init class-level rules on first instance creation.
        # This avoids import-time side effects while keeping the
        # compiled patterns cached for the process lifetime.
        self._init_rules()

    # -- Dictionary matcher (organisation / bank names) -------------------

    def add_organisation(self, name: str) -> None:
        """Add an organisation name to the dictionary matcher.

        This method is used by the feedback loop (Phase 2) to inject
        organisation names learned from user corrections.
        """
        if name not in self._known_organisations:
            self._known_organisations.append(name)

    def _match_dictionaries(self, text: str) -> list[EntitySpan]:
        """Match known organisation and bank names via exact substring search."""
        spans: list[EntitySpan] = []
        for org in self._known_organisations:
            start = 0
            while True:
                pos = text.find(org, start)
                if pos == -1:
                    break
                spans.append(
                    EntitySpan(start=pos, end=pos + len(org), entity_type=EntityType.PRIVATE_PERSON)
                )
                start = pos + 1
        return spans

    # -- Dynamic rules (loaded from storage, Phase 2) -------------------

    _DYNAMIC_RULES: ClassVar[list[tuple[EntityType, re.Pattern[str], str]]] = []

    @classmethod
    def load_dynamic_rules(cls, storage_dir: str | Path) -> int:
        """Load approved rules from the rules storage directory.

        Rules are stored as JSON files in ``{storage_dir}/rules/rule_*.json``.
        Only rules with ``status == "approved"`` are loaded. Previously loaded
        dynamic rules are cleared before re-load.

        Returns:
            The number of rules loaded.
        """
        import json

        rules_dir = Path(storage_dir) / "rules"
        if not rules_dir.is_dir():
            return 0

        with cls._dynamic_rules_lock:
            cls._DYNAMIC_RULES.clear()
        count = 0
        for fpath in sorted(rules_dir.glob("rule_*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if data.get("status") != "approved":
                continue

            pattern_str = data.get("pattern", "")
            entity_type_str = data.get("entity_type", "")
            if not pattern_str or not entity_type_str:
                continue

            try:
                entity_type = EntityType(entity_type_str)
            except ValueError:
                logger.warning(
                    "dynamic rule %s: unknown entity type %r", fpath.stem, entity_type_str
                )
                continue

            try:
                pattern = re.compile(pattern_str)
            except re.error as exc:
                logger.warning(
                    "dynamic rule %s: invalid regex %r — %s", fpath.stem, pattern_str, exc
                )
                continue

            with cls._dynamic_rules_lock:
                cls._DYNAMIC_RULES.append((entity_type, pattern, f"dynamic:{fpath.stem}"))
            count += 1

        if count:
            logger.info("loaded %d dynamic rules from %s", count, rules_dir)
        return count

    # -- Main entry point ------------------------------------------------

    def detect(self, text: str) -> list[EntitySpan]:
        """Run all rules against ``text`` and return non-overlapping spans.

        Results are deduplicated by priority order: rules earlier in
        :attr:`RULES` win over later ones when spans overlap. Dynamic
        rules (loaded via :meth:`load_dynamic_rules`) are appended after
        the static rules, so static rules always win on overlap.

        Returns:
            A list of :class:`EntitySpan` instances, sorted by start offset.
        """
        with self._dynamic_rules_lock:
            all_rules = list(self.RULES) + list(self._DYNAMIC_RULES)
        candidates: list[EntitySpan] = []
        for entity_type, pattern, _name in all_rules:
            for match in pattern.finditer(text):
                candidates.append(
                    EntitySpan(
                        start=match.start(),
                        end=match.end(),
                        entity_type=entity_type,
                    )
                )

        # Add dictionary matches
        candidates.extend(self._match_dictionaries(text))

        return _deduplicate_rules(candidates, all_rules)


# ---------------------------------------------------------------------------
# Regex patterns (module-level for testability)
# ---------------------------------------------------------------------------

# Email — standard pattern
_EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

# Phone — Russian and international (copied from mock client)
_PHONE_PATTERN = r"(?<!\d)(?!\d{16,})\+?\d[\d\s\-()]{7,}\d(?!\d)"

# Bank account with explicit prefix: р/с, Р/с, p/c (latin), P/C
_BANK_ACCOUNT_PATTERN = r"[РрPp][/\\][СсCc]\s*[:/]?\s*(\d{20})"

# Correspondent account with explicit prefix: к/с, К/с
_CORR_ACCOUNT_PATTERN = r"[КкKk][/\\][СсCc]\s*[:/]?\s*(\d{20})"

# BIK (9 digits)
_BIK_PATTERN = r"БИК\s*[:/]?\s*(\d{9})"

# KPP standalone: "КПП 481001001" (9 digits)
# INN_KPP pattern above handles "ИНН/КПП x/y" combined form;
# this catches "КПП x" when it appears without a preceding ИНН.
_KPP_PATTERN = r"КПП\s*[:/]?\s*\d{9}"

# INN/KPP: "ИНН 7743776572" or "ИНН/КПП 7743776572/774301001"
# INN can be 10 digits (legal entity) or 12 digits (individual)
# KPP is 9 digits
_INN_KPP_PATTERN = (
    r"(?:"
    r"ИНН\s*[/:]?\s*\d{10}(?:\d{2})?"  # ИНН 10 или 12 цифр
    r"(?:\s*[/\\]\s*КПП\s*[/:]\s*\d{9})?"  # опционально /КПП
    r")"
)

# OGRN: 13 digits / OGRNIP: 15 digits
_OGRN_PATTERN = r"ОГРН(?:ИП)?\s*[:/]?\s*(\d{13,15})"

# Fallback: standalone 10-15 digit number that looks like a Russian tax ID
# but *isn't* obviously an account number (which is 16-20 digits).
# This is intentionally conservative — only match numbers that are
# clearly separated from surrounding text.
_FALLBACK_RU_ID_PATTERN = r"(?<!\d)\d{10}(?:\d{2,5})?(?!\d)"

# Person name: "Иванов И.И."  (surname + space + initial.initial)
_SURNAME_INITIALS_PATTERN = (
    r"[А-ЯЁ][а-яё]+\s+"  # Фамилия
    r"[А-ЯЁ]\.\s*[А-ЯЁ]\."  # И.О.
)

# Person name: "И.И. Иванов"  (initial.initial + surname)
_INITIALS_SURNAME_PATTERN = (
    r"[А-ЯЁ]\.\s*[А-ЯЁ]\.\s+"  # И.О.
    r"[А-ЯЁ][а-яё]+"  # Фамилия
)

# Full name triple (possibly in non-nominative case):
# "Ханина Андрея Анатольевича", "Соловьев Роман Евгеньевич"
_FULL_NAME_PATTERN = (
    r"[А-ЯЁ][а-яё]+\s+"  # Фамилия
    r"[А-ЯЁ][а-яё]+\s+"  # Имя
    r"[А-ЯЁ][а-яё]+"  # Отчество
)

# Organisation in quotes: ООО «Моторинвест», АО "Рога и Копыта"
_ORG_WITH_QUOTES_PATTERN = (
    r"(?:(?:ООО|АО|ЗАО|ОАО|ПАО|ИП)\s*)"  # legal form
    r"[«\"(]([^»\")]+)[»\")]"  # quoted name
)

# Organisation in brackets for latinised forms
_ORG_WITH_BRACKETS_PATTERN = (
    r"(?:(?:OOO|ZAO|OAO|PAO)\s*)"  # latinised legal form
    r"«([^»]+)»"
)

# Russian postal address pattern
# Examples:
#   125130, г. Москва, Старопетровский пр-д, д.7А, стр.6
#   399672, Липецкая область, Краснинский район, д. Гребенкино, 71
# Matches greedily from postcode through house/building but does NOT
# cross newline boundaries (otherwise it would eat following paragraphs).
# The hyphen is placed last in the character class to avoid range ambiguity.
_ADDRESS_PATTERN = (
    r"\d{6},\s*"  # Postal code + comma
    r"(?:г\.|обл\.|край|респ\.|р-н|район|пос\.|с\.|д\.|дер\.)"  # Region type
    r"[ ,./\d№«»\(\)А-Яа-яёЁ-]+"  # City name + rest (no \n, hyphen last)
)

# Russian text date: "27 июля 2022 года", "«12» декабря 2022г."
_RU_DATE_TEXT_PATTERN = (
    r"\d{1,2}\s+"  # Day
    r"(?:января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)"  # Month
    r"\s+\d{4}\s*(?:г\.|года|год)?"  # Year
)

# Contract/document number: №27072022, № 123/2022
_CONTRACT_NUM_PATTERN = r"[№#]\s*\d{1,10}"  # № + digits


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------


def _priority_rules(
    entity_type: EntityType,
    rules: list[tuple[EntityType, re.Pattern[str], str]],
) -> int:
    """Return the priority index of ``entity_type`` in ``rules``.

    Lower index = higher priority (processed earlier).
    """
    for index, (candidate, _pattern, _name) in enumerate(rules):
        if candidate == entity_type:
            return index
    return len(rules)


def _deduplicate_rules(
    spans: list[EntitySpan],
    rules: list[tuple[EntityType, re.Pattern[str], str]],
) -> list[EntitySpan]:
    """Remove overlapping spans, keeping the highest-priority one.

    This mirrors the dedup logic in :mod:`neironir.privacy.client` but
    operates on the rule-defined priority order rather than the mock
    client's ``_RULES`` list.
    """
    if not spans:
        return []

    ordered = sorted(spans, key=lambda s: (_priority_rules(s.entity_type, rules), s.start))
    kept: list[EntitySpan] = []
    for span in ordered:
        if any(_overlaps_rules(span, kept_span) for kept_span in kept):
            continue
        kept.append(span)
    kept.sort(key=lambda s: (s.start, _priority_rules(s.entity_type, rules)))
    return kept


def _overlaps_rules(a: EntitySpan, b: EntitySpan) -> bool:
    """Return True if spans ``a`` and ``b`` share at least one character."""
    return not (a.end <= b.start or b.end <= a.start)


__all__ = [
    "RuleBasedDetector",
]
