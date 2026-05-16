"""Policy Engine public API.

Primary entry point:
    prepare(request) → PreparedPackage

For max_allowable strictness:
    find_max_allowable_strictness(request, profile) → PreparedPackage
"""

from __future__ import annotations

import logging

from app.policy_engine import audit
from app.policy_engine.models import MaxAllowableDecision, PolicyRequest, PreparedPackage
from app.policy_engine.package import save_package
from app.policy_engine.profiles import ProfileConfig, load_profile
from app.policy_engine.risk import score_risk
from app.policy_engine.strictness import (
    STRICTNESS_ORDER,
    effective_strictness,
    strictness_index,
)
from app.policy_engine.transformer import transform
from app.policy_engine.usefulness import score_usefulness

logger = logging.getLogger(__name__)

TASK_TO_PROFILE: dict[str, str] = {
    "translation":          "translation",
    "clinical_summary":     "clinical_summary",
    "ml_feature_extraction":"ml_feature_extraction",
    "research_extract":     "research_extract",
    "public_release":       "public_release",
}


def _run_once(
    request: PolicyRequest,
    profile: ProfileConfig,
    strictness: str,
) -> PreparedPackage:
    """Run the full transformation + scoring pipeline at one strictness level."""
    prepared_text, log, token_map = transform(request, profile, strictness)

    usefulness = score_usefulness(request.document_text, prepared_text, profile)
    risk = score_risk(prepared_text, log)

    pkg = PreparedPackage(
        job_id=request.job_id,
        profile=profile.name,
        selected_strictness=strictness,
        prepared_text=prepared_text,
        transformation_log=log,
        usefulness=usefulness,
        risk=risk,
        policy_token_map=token_map,
    )
    return pkg


def find_max_allowable_strictness(
    request: PolicyRequest,
    profile: ProfileConfig,
) -> PreparedPackage:
    """Iterate from strictest to most permissive; return the strictest that passes usefulness."""
    audit.policy_preparation_started(
        request.job_id, request.task, request.consumer_type, request.provider_risk
    )

    provider_floor = effective_strictness("minimal", request.provider_risk)
    floor_idx = strictness_index(provider_floor)

    # Only try levels at or above the provider floor
    candidates = [lvl for lvl in reversed(STRICTNESS_ORDER)
                  if strictness_index(lvl) >= floor_idx]

    best: PreparedPackage | None = None
    levels_tried: list[str] = []
    scores: dict[str, float] = {}

    for level in candidates:
        logger.info(f"  max_allowable trying strictness={level} for job {request.job_id}")
        pkg = _run_once(request, profile, level)
        levels_tried.append(level)
        scores[level] = pkg.usefulness.score
        audit.policy_usefulness_checked(request.job_id, pkg.usefulness.score, pkg.usefulness.passes, request.task)
        if pkg.usefulness.passes:
            best = pkg
            break

    if best is None:
        best = _run_once(request, profile, candidates[-1])
        levels_tried.append(candidates[-1])
        scores[candidates[-1]] = best.usefulness.score
        reason = f"No level passed the usefulness threshold — fell back to {candidates[-1]}"
    else:
        reason = (
            f"Selected '{best.selected_strictness}': first level passing usefulness threshold "
            f"({best.usefulness.score:.2f} ≥ {best.usefulness.threshold:.2f})"
        )

    best.max_allowable_decision = MaxAllowableDecision(
        levels_tried=levels_tried,
        scores_by_level=scores,
        selected=best.selected_strictness,
        reason=reason,
        provider_floor=provider_floor,
    )

    audit.policy_strictness_selected(request.job_id, best.selected_strictness, "max_allowable")
    return best


def prepare(request: PolicyRequest, save: bool = True) -> PreparedPackage:
    """Main entry point. Prepares a document according to the policy request."""
    profile_name = TASK_TO_PROFILE.get(request.task, request.task)
    profile = load_profile(profile_name)

    audit.policy_profile_selected(
        request.job_id, request.task, profile.name,
        request.strictness_mode
    )
    audit.policy_preparation_started(
        request.job_id, request.task, request.consumer_type, request.provider_risk
    )

    if request.strictness_mode == "max_allowable":
        pkg = find_max_allowable_strictness(request, profile)
    else:
        strictness = effective_strictness(request.strictness_mode, request.provider_risk)
        pkg = _run_once(request, profile, strictness)
        audit.policy_strictness_selected(request.job_id, strictness, request.strictness_mode)

    audit.policy_transformation_applied(request.job_id, len(pkg.transformation_log), pkg.selected_strictness)
    audit.policy_risk_checked(request.job_id, pkg.risk.risk_score)

    if save:
        pkg_dir = save_package(pkg, request, profile)
        manifest = pkg.manifest
        audit.policy_package_created(
            request.job_id, pkg.package_id,
            manifest.get("recommended_action", "unknown")
        )
        logger.info(
            f"Package {pkg.package_id} prepared for job {request.job_id} "
            f"[task={request.task} strictness={pkg.selected_strictness} "
            f"risk={pkg.risk.risk_score} usefulness={pkg.usefulness.score:.2f}]"
        )

    return pkg
