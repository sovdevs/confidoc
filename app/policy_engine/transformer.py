"""Core transformation engine: applies profile rules to approved entities.

Works on the original document text + approved Entity objects.
Produces:
  - prepared_text       : transformed markdown for Zone 2
  - transformation_log  : audit trail of every entity action
  - token_map           : {token: original_text} — Zone 1 only, never exported

Entity handling:
  approved=True, dismissed=False  → transform according to profile rule
  dismissed=True                  → leave original text unchanged
  approved=False, dismissed=False → warn as PENDING, do not transform
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from app.policy_engine.models import PolicyRequest, TransformationEntry
from app.policy_engine.profiles import EntityRule, ProfileConfig
from app.policy_engine.strictness import resolve_action

# ── German city → region mapping (P1 subset) ─────────────────────────────────
_CITY_TO_REGION: dict[str, str] = {
    "münchen": "Bayern", "munich": "Bayern", "nürnberg": "Bayern", "augsburg": "Bayern",
    "amberg": "Bayern", "regensburg": "Bayern", "würzburg": "Bayern",
    "frankfurt": "Hessen", "wiesbaden": "Hessen", "darmstadt": "Hessen",
    "berlin": "Berlin", "hamburg": "Hamburg", "bremen": "Bremen",
    "köln": "Nordrhein-Westfalen", "cologne": "Nordrhein-Westfalen",
    "düsseldorf": "Nordrhein-Westfalen", "dortmund": "Nordrhein-Westfalen",
    "stuttgart": "Baden-Württemberg", "mannheim": "Baden-Württemberg",
    "hannover": "Niedersachsen", "braunschweig": "Niedersachsen",
    "leipzig": "Sachsen", "dresden": "Sachsen",
    "trier": "Rheinland-Pfalz", "mainz": "Rheinland-Pfalz",
    "kiel": "Schleswig-Holstein", "lübeck": "Schleswig-Holstein",
    "erfurt": "Thüringen", "jena": "Thüringen",
    "hohenfels": "Bayern", "vilseck": "Bayern",
}

_CITY_TO_COUNTRY: dict[str, str] = {city: "Deutschland" for city in _CITY_TO_REGION}

# ── DOB parsing ───────────────────────────────────────────────────────────────
_DOB_PATTERNS = [
    re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{4})'),   # DD.MM.YYYY
    re.compile(r'(\d{4})-(\d{2})-(\d{2})'),               # YYYY-MM-DD
]


def _parse_date(text: str) -> Optional[date]:
    for pattern in _DOB_PATTERNS:
        m = pattern.fullmatch(text.strip())
        if m:
            groups = m.groups()
            try:
                if len(groups[0]) == 4:   # YYYY-MM-DD
                    return date(int(groups[0]), int(groups[1]), int(groups[2]))
                return date(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                pass
    return None


def _calculate_age(dob: date, ref: date) -> int:
    age = ref.year - dob.year
    if (ref.month, ref.day) < (dob.month, dob.day):
        age -= 1
    return max(0, age)


def _age_band(age: int) -> str:
    low = (age // 10) * 10
    return f"{low}-{low + 9}"


# ── Token counter ─────────────────────────────────────────────────────────────

class _TokenCounter:
    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._identity: dict[tuple[str, str], str] = {}  # (token_type, text) → token
        # Occurrence-based types get a new token each time
        self._occurrence_based = {"DATE", "REPORT_DATE", "VISIT_DATE"}

    def get(self, token_type: str, original_text: str) -> str:
        if token_type in self._occurrence_based:
            self._counters[token_type] += 1
            return f"[{token_type}_{self._counters[token_type]:03d}]"
        key = (token_type, original_text.strip())
        if key not in self._identity:
            self._counters[token_type] += 1
            self._identity[key] = f"[{token_type}_{self._counters[token_type]:03d}]"
        return self._identity[key]


# ── Date ordering for relative_date offset mode ───────────────────────────────

def _find_anchor_date(entities: list, anchor_label: str, doc_date: Optional[str]) -> Optional[date]:
    for e in entities:
        if e.approved and not e.dismissed and e.label == anchor_label:
            d = _parse_date(e.text)
            if d:
                return d
    if doc_date:
        return _parse_date(doc_date)
    return None


# ── Location generalization ───────────────────────────────────────────────────

def _generalize_location(text: str, mode: str) -> str:
    city_key = text.strip().lower()
    if mode == "city_to_region":
        region = _CITY_TO_REGION.get(city_key)
        if region:
            return region
        return "[REGION]"
    elif mode == "city_to_country":
        country = _CITY_TO_COUNTRY.get(city_key, "[COUNTRY]")
        return country
    elif mode == "remove":
        return "[LOCATION_REMOVED]"
    else:  # exact_to_placeholder — use numbered token (handled by caller)
        return None  # caller falls back to stable_token logic


# ── Core transformation ───────────────────────────────────────────────────────

def transform(
    request: PolicyRequest,
    profile: ProfileConfig,
    strictness: str,
) -> tuple[str, list[TransformationEntry], dict[str, str]]:
    """Apply profile rules to the document text.

    Returns:
        prepared_text      : transformed markdown for Zone 2
        transformation_log : one entry per entity processed
        token_map          : {token: original} — Zone 1 / rehydration use only
    """
    text = request.document_text
    entities = request.entities  # list[Entity]
    token_counter = _TokenCounter()
    token_map: dict[str, str] = {}
    log: list[TransformationEntry] = []
    warnings: list[str] = []

    # Sort entities by position descending so replacements don't shift offsets
    approved = sorted(
        [e for e in entities if e.approved and not e.dismissed],
        key=lambda e: e.start, reverse=True,
    )
    pending = [e for e in entities if not e.approved and not e.dismissed]

    if pending:
        warnings.append(f"PENDING_ENTITIES: {len(pending)} entity/entities not yet reviewed — not transformed")

    # Find reference date for DOB calculations
    ref_date_for_dob = _find_anchor_date(entities, "REPORT_DATE", request.document_date) \
                       or date.today()

    for e in approved:
        rule: EntityRule = profile.rule_for(e.label)
        base_action = rule.action
        action = resolve_action(base_action, strictness, profile, e.label)

        output = _apply_action(
            action=action,
            entity=e,
            rule=rule,
            token_counter=token_counter,
            token_map=token_map,
            ref_date=ref_date_for_dob,
            profile=profile,
            request=request,
            entities=entities,
        )

        log.append(TransformationEntry(
            entity_id=e.id,
            label=e.label,
            original_text=e.text,
            action_applied=action,
            output=output,
            strictness=strictness,
            warning=rule.flag,
        ))

        # Replace in text (we're going right-to-left)
        text = text[:e.start] + output + text[e.end:]

    return text, log, token_map


def _apply_action(
    action: str,
    entity,
    rule: EntityRule,
    token_counter: _TokenCounter,
    token_map: dict[str, str],
    ref_date: date,
    profile: ProfileConfig,
    request: PolicyRequest,
    entities: list,
) -> str:
    """Return the replacement string for one entity."""

    if action == "keep":
        return entity.text

    if action == "flag_for_review":
        # Keep text in prepared doc; risk report will note it
        return entity.text

    if action == "remove":
        label = entity.label.replace("_", " ")
        return f"[REMOVED_{entity.label}]"

    if action == "stable_token":
        token_type = rule.token_type or entity.label
        token = token_counter.get(token_type, entity.text)
        token_map[token] = entity.text
        return token

    if action in ("age_from_dob", "age_band_from_dob"):
        dob = _parse_date(entity.text)
        if dob is None:
            return f"[AGE_UNKNOWN]"
        age = _calculate_age(dob, ref_date)
        if action == "age_band_from_dob":
            return f"age band {_age_band(age)}"
        return f"age {age}"

    if action in ("relative_date", "coarse_relative_date"):
        anchor = _find_anchor_date(entities, rule.relative_date_anchor, request.document_date)
        if rule.relative_date_mode == "offset" and anchor:
            d = _parse_date(entity.text)
            if d:
                delta = (d - anchor).days
                if action == "coarse_relative_date":
                    # Round to nearest week
                    weeks = round(delta / 7)
                    sign = "+" if weeks >= 0 else ""
                    return f"[~{sign}{weeks}w from report]"
                sign = "+" if delta >= 0 else ""
                return f"Day {sign}{delta}"
        # Fall back to numbered token
        token_type = rule.token_type or entity.label
        token = token_counter.get(token_type, entity.text)
        if entity.label not in ("REPORT_DATE", "VISIT_DATE"):
            token_type = "DATE"
            token = token_counter.get(token_type, entity.text)
        token_map[token] = entity.text
        if action == "coarse_relative_date":
            # If no anchor, warn
            token += "≈"
        return token

    if action == "generalize":
        gen = _generalize_location(entity.text, rule.location_mode)
        if gen is not None:
            return gen
        # Fallback to stable placeholder
        token_type = "LOCATION"
        token = token_counter.get(token_type, entity.text)
        token_map[token] = entity.text
        return token

    if action == "date_shift":
        # Deterministic shift seeded by job_id — consistent within a job
        seed = int(hashlib.md5(request.job_id.encode()).hexdigest()[:8], 16)
        shift_days = (seed % 731) - 365  # -365 to +365
        d = _parse_date(entity.text)
        if d is None:
            token = token_counter.get("DATE", entity.text)
            token_map[token] = entity.text
            return token
        from datetime import timedelta
        shifted = d + timedelta(days=shift_days)
        return shifted.strftime("%d.%m.%Y")

    # Unknown action: keep as-is with a note
    return entity.text
