"""Deterministic usefulness checks for prepared documents.

Checks inspect the prepared text heuristically for preservation of
task-relevant content. No LLM dependency in P1.

The weighted score formula:
  score = sum(weight_i * 1.0 if check_i passes else 0.0)
  clamped to [0.0, 1.0]
"""

from __future__ import annotations

import re

from app.policy_engine.models import UsefulnessResult
from app.policy_engine.profiles import ProfileConfig, UsefulnessConfig

# ── Heuristic patterns ────────────────────────────────────────────────────────
_DIAGNOSIS_TERMS = re.compile(
    r'\b(?:diagnos|icd|erkrank|befund|symptom|syndrom|diagnosis|disorder|condition|'
    r'infekt|tumor|karzinom|myom|zyste|fraktur|stenose)', re.I
)
_MEDICATION_TERMS = re.compile(
    r'\b(mg|mcg|µg|tablet|kapsel|capsule|injektion|injection|infusion|'
    r'dose|dosis|prophylaxe|therapie|therapy|medikament|medication|'
    r'ferrosanol|jodid|iodide|analgetika|antibiotik)\b', re.I
)
_MEASUREMENT_TERMS = re.compile(
    r'\b\d+[,.]?\d*\s*(?:g|kg|mg|ml|cm|mm|mmhg|bpm|%|g/dl|mmol/l|iu/l|°c)\b', re.I
)
_PROCEDURE_TERMS = re.compile(
    r'\b(sonograph|ultraschall|ultrasound|mrt|ct|röntgen|x-ray|operation|'
    r'chirurg|surgery|biopsie|biopsy|endoskop|katheter|sectio|geburt|delivery)\b', re.I
)
_DATE_PATTERN = re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|Day [+\-]\d+|\[DATE_\d+\]')
_PLACEHOLDER_PATTERN = re.compile(r'\[[A-Z][A-Z_]*_\d{3}\]')
_SECTION_HEADING = re.compile(r'^#{1,4}\s+\S', re.MULTILINE)
_MIN_TEXT_CHARS = 100


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_diagnoses_preserved(original: str, prepared: str) -> bool:
    return bool(_DIAGNOSIS_TERMS.search(prepared))

def _check_medications_preserved(original: str, prepared: str) -> bool:
    return bool(_MEDICATION_TERMS.search(prepared))

def _check_measurements_preserved(original: str, prepared: str) -> bool:
    return bool(_MEASUREMENT_TERMS.search(prepared))

def _check_procedures_preserved(original: str, prepared: str) -> bool:
    return bool(_PROCEDURE_TERMS.search(prepared))

def _check_chronology_preserved(original: str, prepared: str) -> bool:
    # Any dates or chronology markers remain
    return bool(_DATE_PATTERN.search(prepared))

def _check_placeholders_stable(original: str, prepared: str) -> bool:
    # All tokens in prepared follow stable format
    tokens = _PLACEHOLDER_PATTERN.findall(prepared)
    return len(tokens) == len(set(tokens)) or len(tokens) == 0

def _check_section_structure_preserved(original: str, prepared: str) -> bool:
    orig_headings = len(_SECTION_HEADING.findall(original))
    prep_headings = len(_SECTION_HEADING.findall(prepared))
    if orig_headings == 0:
        return True
    return prep_headings >= (orig_headings * 0.5)

def _check_enough_text_remaining(original: str, prepared: str) -> bool:
    if len(original) == 0:
        return True
    ratio = len(prepared.strip()) / max(len(original.strip()), 1)
    return ratio >= 0.30 and len(prepared.strip()) >= _MIN_TEXT_CHARS

def _check_terminology_preserved(original: str, prepared: str) -> bool:
    # Clinical or domain terminology still present
    return (bool(_DIAGNOSIS_TERMS.search(prepared))
            or bool(_MEDICATION_TERMS.search(prepared))
            or bool(_PROCEDURE_TERMS.search(prepared)))

def _check_sentence_meaning_preserved(original: str, prepared: str) -> bool:
    # Proxy: most words from original are still present (tokens replace names, not content)
    orig_words = set(re.findall(r'\b[a-zA-ZäöüÄÖÜß]{4,}\b', original.lower()))
    prep_words = set(re.findall(r'\b[a-zA-ZäöüÄÖÜß]{4,}\b', prepared.lower()))
    if not orig_words:
        return True
    overlap = orig_words & prep_words
    return len(overlap) / len(orig_words) >= 0.50

def _check_gender_context_preserved(original: str, prepared: str) -> bool:
    gender_terms = re.compile(r'\b(er|sie|sein|ihr|patient|patientin|herr|frau|'
                               r'he|she|his|her|male|female|männlich|weiblich)\b', re.I)
    return bool(gender_terms.search(prepared))

def _check_coded_diagnoses_preserved(original: str, prepared: str) -> bool:
    icd = re.compile(r'\b[A-Z]\d{2}(?:\.\d{1,2})?\b')
    return bool(icd.search(prepared)) or bool(_DIAGNOSIS_TERMS.search(prepared))

def _check_demographic_band_preserved(original: str, prepared: str) -> bool:
    return bool(re.search(r'\bage\s*(?:band\s*)?\d', prepared, re.I))

def _check_enough_structured_content_remaining(original: str, prepared: str) -> bool:
    return _check_enough_text_remaining(original, prepared)

def _check_treatment_plan_preserved(original: str, prepared: str) -> bool:
    terms = re.compile(r'\b(therapie|treatment|plan|empfehlung|recommendation|'
                        r'follow.?up|control|kontrolle|verordnung)\b', re.I)
    return bool(terms.search(prepared))

def _check_general_topic_preserved(original: str, prepared: str) -> bool:
    return len(prepared.strip()) > 50

def _check_no_direct_identifiers(original: str, prepared: str) -> bool:
    # Simple: no remaining proper-noun-like capitalized full names
    # (This is a heuristic — not foolproof)
    name_pattern = re.compile(r'\b[A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ][a-zäöüß]+\b')
    return not bool(name_pattern.search(prepared))

def _check_enough_context_for_demo(original: str, prepared: str) -> bool:
    return len(prepared.strip()) >= 50


# ── Check registry ────────────────────────────────────────────────────────────

_CHECK_FUNCTIONS: dict[str, callable] = {
    "diagnoses_preserved":                  _check_diagnoses_preserved,
    "medications_preserved":                _check_medications_preserved,
    "measurements_preserved":               _check_measurements_preserved,
    "procedures_preserved":                 _check_procedures_preserved,
    "chronology_preserved":                 _check_chronology_preserved,
    "placeholders_stable":                  _check_placeholders_stable,
    "section_structure_preserved":          _check_section_structure_preserved,
    "enough_text_remaining":                _check_enough_text_remaining,
    "terminology_preserved":                _check_terminology_preserved,
    "sentence_meaning_preserved":           _check_sentence_meaning_preserved,
    "gender_context_preserved":             _check_gender_context_preserved,
    "coded_diagnoses_preserved":            _check_coded_diagnoses_preserved,
    "demographic_band_preserved":           _check_demographic_band_preserved,
    "enough_structured_content_remaining":  _check_enough_structured_content_remaining,
    "treatment_plan_preserved":             _check_treatment_plan_preserved,
    "general_topic_preserved":             _check_general_topic_preserved,
    "no_direct_identifiers":                _check_no_direct_identifiers,
    "enough_context_for_demo":              _check_enough_context_for_demo,
}


# ── Scorer ────────────────────────────────────────────────────────────────────

def score_usefulness(
    original_text: str,
    prepared_text: str,
    profile: ProfileConfig,
) -> UsefulnessResult:
    cfg: UsefulnessConfig = profile.usefulness
    threshold = cfg.threshold
    weights = cfg.weights

    if not weights:
        # Fallback: run all checks equally weighted
        weights = {k: 1.0 / len(_CHECK_FUNCTIONS) for k in _CHECK_FUNCTIONS}

    check_results: dict[str, bool] = {}
    notes: list[str] = []
    score = 0.0

    # Baseline checks always run (not weighted, but always in check_results)
    check_results["enough_text_remaining"] = _check_enough_text_remaining(original_text, prepared_text)
    check_results["placeholders_stable"] = _check_placeholders_stable(original_text, prepared_text)
    if not check_results["enough_text_remaining"]:
        notes.append("Check failed: enough_text_remaining")

    for check_name, weight in weights.items():
        fn = _CHECK_FUNCTIONS.get(check_name)
        if fn is None:
            notes.append(f"Unknown check '{check_name}' — skipped")
            continue
        passed = fn(original_text, prepared_text)
        check_results[check_name] = passed
        if passed:
            score += weight
        else:
            notes.append(f"Check failed: {check_name}")

    score = min(1.0, max(0.0, score))
    return UsefulnessResult(
        score=round(score, 3),
        passes=score >= threshold,
        threshold=threshold,
        checks=check_results,
        notes=notes,
    )
