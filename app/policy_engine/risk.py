"""Deterministic risk checks for prepared documents.

Checks whether the prepared text still contains re-identification risk.
All checks are deterministic — no LLM dependency in P1.
"""

from __future__ import annotations

import re

from app.policy_engine.models import RiskResult

# ── Risk patterns ─────────────────────────────────────────────────────────────
_FULL_NAME       = re.compile(r'\b[A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ][a-zäöüß]+\b')
_ADDRESS         = re.compile(
    r'\b\d{1,5}\s+[A-ZÄÖÜ][a-zäöüß]+(?:straße|weg|allee|platz|str\.)\b'
    r'|\b[A-ZÄÖÜ][a-zäöüß]+(?:straße|weg|allee|platz)\s+\d+\b', re.I
)
_EMAIL           = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
_PHONE           = re.compile(r'\b(?:\+\d{1,3}[\s\-]?)?\(?\d{2,5}\)?[\s\-/]\d{3,8}\b')
_INSURANCE_ID    = re.compile(r'\b[A-Z]\d{9,12}\b')
_EXACT_DOB       = re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b')
_GERMAN_POSTAL   = re.compile(r'\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+\b')
_RARE_CONDITION  = re.compile(
    r'\b(epilepsie|epilepsy|rare\s+disease|orphan|selten\s+erkrank|'
    r'phenylketonur|mucoviszidose|cystic\s+fibrosis|huntington|'
    r'amyloidose|amyloidosis)\b', re.I
)
_PRECISE_DATE    = re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b')
_PRECISE_LOCATION = re.compile(r'\b\d{5}\b')  # postal code


def _count_precise_dates(text: str) -> int:
    return len(_PRECISE_DATE.findall(text))


def score_risk(
    prepared_text: str,
    transformation_log: list,
    entities_processed: list | None = None,
) -> RiskResult:
    checks: dict[str, bool] = {}
    warnings: list[str] = []

    checks["no_full_names"]       = not bool(_FULL_NAME.search(prepared_text))
    checks["no_addresses"]        = not bool(_ADDRESS.search(prepared_text))
    checks["no_emails"]           = not bool(_EMAIL.search(prepared_text))
    checks["no_phone_numbers"]    = not bool(_PHONE.search(prepared_text))
    checks["no_insurance_ids"]    = not bool(_INSURANCE_ID.search(prepared_text))
    checks["no_exact_dob"]        = not bool(_EXACT_DOB.search(prepared_text))
    checks["no_postal_codes"]     = not bool(_GERMAN_POSTAL.search(prepared_text))
    checks["rare_condition_clear"]= not bool(_RARE_CONDITION.search(prepared_text))

    precise_date_count = _count_precise_dates(prepared_text)
    checks["few_precise_dates"] = precise_date_count <= 2

    # Warn for each failed direct-identifier check
    if not checks["no_full_names"]:
        warnings.append("Full names may still be present in prepared document")
    if not checks["no_addresses"]:
        warnings.append("Addresses may still be present")
    if not checks["no_emails"]:
        warnings.append("Email addresses may still be present")
    if not checks["no_phone_numbers"]:
        warnings.append("Phone numbers may still be present")
    if not checks["no_insurance_ids"]:
        warnings.append("Insurance / ID numbers may still be present")
    if not checks["no_exact_dob"]:
        warnings.append("Exact date-of-birth may still be present")
    if not checks["rare_condition_clear"]:
        warnings.append("Rare condition detected — quasi-identifier risk; recommend review")
    if not checks["few_precise_dates"]:
        warnings.append(f"{precise_date_count} precise dates remain — consider relative_date transformation")

    # Check flagged entities from transformation log
    flagged = [e for e in (transformation_log or []) if e.warning]
    if flagged:
        for entry in flagged:
            warnings.append(f"Entity '{entry.label}' flagged: {entry.warning}")
        checks["no_flagged_entities"] = False
    else:
        checks["no_flagged_entities"] = True

    # Score
    direct_checks = ["no_full_names", "no_addresses", "no_emails",
                     "no_phone_numbers", "no_insurance_ids", "no_exact_dob"]
    quasi_checks  = ["rare_condition_clear", "few_precise_dates",
                     "no_postal_codes", "no_flagged_entities"]

    direct_pass = sum(1 for c in direct_checks if checks.get(c, True))
    quasi_pass  = sum(1 for c in quasi_checks  if checks.get(c, True))

    direct_risk = "low"   if direct_pass == len(direct_checks) else \
                  "medium" if direct_pass >= len(direct_checks) - 1 else "high"
    quasi_risk  = "low"   if quasi_pass  == len(quasi_checks)  else \
                  "medium" if quasi_pass  >= len(quasi_checks)  - 1 else "high"

    risk_levels = {"low": 0, "medium": 1, "high": 2}
    overall = max(direct_risk, quasi_risk, key=lambda r: risk_levels[r])

    return RiskResult(
        risk_score=overall,
        direct_identifier_risk=direct_risk,
        quasi_identifier_risk=quasi_risk,
        checks=checks,
        warnings=warnings,
    )
