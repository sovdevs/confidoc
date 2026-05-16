"""Data models for the Confidoc Policy Engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class PolicyRequest:
    """Input to the Policy Engine from a Zone 2 task request."""
    job_id: str
    task: str                         # translation | clinical_summary | ...
    strictness_mode: str              # minimal|balanced|strict|maximum|max_allowable
    consumer_type: str                # internal_clinician|external_translator|cloud_llm|...
    provider_risk: str                # trusted_internal|trusted_vendor|cloud_llm|local_llm|...
    document_text: str                # original extracted markdown (Zone 1 only)
    entities: list                    # list[Entity] from jobs.py — approved/dismissed/pending
    document_date: Optional[str] = None   # ISO or DD.MM.YYYY — used for DOB age calculation
    source_language: str = "de-DE"
    target_language: str = "en-GB"


@dataclass
class TransformationEntry:
    """One record in the transformation log — what was done to one entity."""
    entity_id: str
    label: str
    original_text: str
    action_applied: str               # stable_token | remove | age_from_dob | ...
    output: str                       # what appears in the prepared doc
    strictness: str
    warning: Optional[str] = None


@dataclass
class UsefulnessResult:
    score: float
    passes: bool
    threshold: float
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class RiskResult:
    risk_score: str                   # low | medium | high
    direct_identifier_risk: str
    quasi_identifier_risk: str
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MaxAllowableDecision:
    """Records the strictness-selection loop outcome for audit and UI display."""
    levels_tried: list[str] = field(default_factory=list)
    scores_by_level: dict[str, float] = field(default_factory=dict)
    selected: str = ""
    reason: str = ""
    provider_floor: str = ""


@dataclass
class PreparedPackage:
    package_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_id: str = ""
    profile: str = ""
    selected_strictness: str = ""
    prepared_text: str = ""
    transformation_log: list[TransformationEntry] = field(default_factory=list)
    usefulness: Optional[UsefulnessResult] = None
    risk: Optional[RiskResult] = None
    manifest: dict[str, Any] = field(default_factory=dict)
    policy_token_map: dict[str, str] = field(default_factory=dict)  # Zone 1 only — NEVER exported
    warnings: list[str] = field(default_factory=list)
    prepared_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_allowable_decision: Optional[MaxAllowableDecision] = None
