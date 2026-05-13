"""OCR artefact detection for pseudonymized Markdown.

Operates ONLY on pseudonymized text (stable tokens like [PATIENT_NAME_001]).
All checks skip token spans — tokens are never flagged, modified, or broken.

Token validation enforces that the multiset of tokens in submitted edits
exactly matches the multiset in the source pseudonymized text.
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Optional

# Matches any stable pseudonymization token
_TOKEN_RE = re.compile(r'\[[A-Z][A-Z_]*_\d{3}\]')


@dataclass
class OcrFlag:
    start: int            # char offset in the text
    end: int
    text: str             # the flagged substring
    suggestion: Optional[str]  # proposed correction; None when ambiguous
    rule: str
    message: str
    severity: str         # "error" | "warning"

    def as_dict(self) -> dict:
        return asdict(self)


# ── Token utilities ───────────────────────────────────────────────────────────

def token_ranges(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def extract_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def validate_tokens(original: str, edited: str) -> list[str]:
    """Return list of error strings if token multisets differ, else empty list."""
    orig = Counter(extract_tokens(original))
    new  = Counter(extract_tokens(edited))
    errors = []
    for token, count in orig.items():
        if new[token] < count:
            errors.append(f"{token}: expected {count}, found {new[token]}")
    for token, count in new.items():
        if token not in orig:
            errors.append(f"{token}: appeared in edit but not in source")
    return errors


def _in_token(start: int, end: int, tok_ranges: list[tuple[int, int]]) -> bool:
    return any(ts <= start and end <= te for ts, te in tok_ranges)


# ── Detection rules ───────────────────────────────────────────────────────────
# Each entry: (pattern, suggestion_fn | None, rule_id, message, severity)

def _b_suggestion(m: re.Match) -> str:
    return "ß"

def _space_after(m: re.Match) -> str:
    # Match is just the punctuation char (lookbehind/lookahead capture the context).
    # Suggestion: keep the punctuation and insert a space after it.
    return m.group() + " "

_RULES: list[tuple[re.Pattern, Optional[callable], str, str, str]] = [

    # ── High-confidence errors ────────────────────────────────────────────────

    # Uppercase B between lowercase letters → ß
    # vergoBerter → vergrößerter, GroBe → Große, MaBnahmen → Maßnahmen
    (re.compile(r'(?<=[a-zäöü])B(?=[a-zäöüß])'),
     _b_suggestion,
     "B_AS_SS",
     "Likely OCR: uppercase B should be ß (e.g. GroBe → Große)",
     "error"),

    # Digit glued inside a word (no surrounding whitespace)
    # Sonokontrollein6Monaten → Sonokontrolle in 6 Monaten
    (re.compile(r'(?<=[A-Za-zäöüÄÖÜß])\d+(?=[A-Za-zäöüÄÖÜß])'),
     None,
     "DIGIT_IN_WORD",
     "Digit glued into word — insert spaces (e.g. in6Monaten → in 6 Monaten)",
     "error"),

    # Known whole-word OCR substitutions
    (re.compile(r'\bSehir\b'),
     lambda m: "Sehr",
     "OCR_SEHIR",
     "Likely OCR: 'Sehir' → 'Sehr'",
     "error"),

    (re.compile(r'\bGruben\b'),
     lambda m: "Grüßen",
     "OCR_GRUBEN",
     "Likely OCR: 'Gruben' → 'Grüßen' (letter closing)",
     "error"),

    (re.compile(r'\bfur\b'),
     lambda m: "für",
     "OCR_FUR",
     "Likely OCR: 'fur' → 'für'",
     "error"),

    (re.compile(r'\bauffallig\b', re.I),
     lambda m: "auffällig",
     "OCR_AUFFALLIG",
     "Likely OCR: missing umlaut (auffallig → auffällig)",
     "error"),

    # ── Spacing warnings ──────────────────────────────────────────────────────

    # Missing space after sentence-ending punctuation before uppercase letter
    # "Befund.Die" → "Befund. Die"
    (re.compile(r'(?<=[a-zäöüA-ZÄÖÜ])[.!?](?=[A-ZÄÖÜ])'),
     _space_after,
     "MISSING_SPACE_SENTENCE",
     "Missing space after sentence-ending punctuation",
     "warning"),

    # Colon or period glued mid-word (not at end of line)
    # "Ov:Zyste" → "Ov-Zyste" or "Ov: Zyste"
    (re.compile(r'(?<=[a-zäöü])[:\.](?=[a-zäöüA-ZÄÖÜ])'),
     None,
     "PUNCT_IN_WORD",
     "Punctuation glued inside word — likely OCR artefact or missing space",
     "warning"),

    # ── Formatting warnings ───────────────────────────────────────────────────

    # Trailing whitespace on line
    (re.compile(r'[ \t]+$', re.MULTILINE),
     lambda m: "",
     "TRAILING_WHITESPACE",
     "Trailing whitespace",
     "warning"),

    # Multiple consecutive blank lines (more than 2)
    (re.compile(r'\n{4,}'),
     lambda m: "\n\n\n",
     "EXCESS_BLANK_LINES",
     "More than two consecutive blank lines",
     "warning"),
]


# ── Public API ────────────────────────────────────────────────────────────────

def detect(text: str) -> list[OcrFlag]:
    """Return all OCR flags in *text*, skipping any stable token spans."""
    tok_ranges = token_ranges(text)
    flags: list[OcrFlag] = []
    seen: set[tuple[int, int]] = set()

    for pattern, sugg_fn, rule, message, severity in _RULES:
        for m in pattern.finditer(text):
            if _in_token(m.start(), m.end(), tok_ranges):
                continue
            span = (m.start(), m.end())
            if span in seen:
                continue
            seen.add(span)
            flags.append(OcrFlag(
                start=m.start(), end=m.end(),
                text=m.group(),
                suggestion=sugg_fn(m) if sugg_fn else None,
                rule=rule, message=message, severity=severity,
            ))

    return sorted(flags, key=lambda f: f.start)


def apply_suggestions(text: str) -> tuple[str, int]:
    """Apply all unambiguous (non-None) suggestions right-to-left.
    Returns (corrected_text, number_of_fixes_applied).
    Skips token spans.
    """
    flags = [f for f in detect(text) if f.suggestion is not None]
    flags_rtl = sorted(flags, key=lambda f: f.start, reverse=True)
    count = 0
    for f in flags_rtl:
        text = text[: f.start] + f.suggestion + text[f.end :]
        count += 1
    return text, count
