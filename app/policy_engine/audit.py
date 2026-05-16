"""Policy Engine audit events — never log raw PHI."""

from __future__ import annotations
from app.storage.audit_log import log as _log


def policy_profile_selected(job_id: str, task: str, profile: str, strictness: str) -> None:
    _log(job_id, "POLICY_PROFILE_SELECTED", {"task": task, "profile": profile, "strictness": strictness})


def policy_preparation_started(job_id: str, task: str, consumer_type: str, provider_risk: str) -> None:
    _log(job_id, "POLICY_PREPARATION_STARTED", {"task": task, "consumer_type": consumer_type, "provider_risk": provider_risk})


def policy_transformation_applied(job_id: str, entity_count: int, strictness: str) -> None:
    _log(job_id, "POLICY_TRANSFORMATION_APPLIED", {"entities_processed": entity_count, "strictness": strictness})


def policy_usefulness_checked(job_id: str, score: float, passes: bool, task: str) -> None:
    _log(job_id, "POLICY_USEFULNESS_CHECKED", {"score": score, "passes": passes, "task": task})


def policy_risk_checked(job_id: str, risk_score: str) -> None:
    _log(job_id, "POLICY_RISK_CHECKED", {"risk_score": risk_score})


def policy_strictness_selected(job_id: str, selected: str, mode: str) -> None:
    _log(job_id, "POLICY_STRICTNESS_SELECTED", {"selected": selected, "mode": mode})


def policy_package_created(job_id: str, package_id: str, recommended_action: str) -> None:
    _log(job_id, "POLICY_PACKAGE_CREATED", {"package_id": package_id, "recommended_action": recommended_action})


def policy_package_exported(job_id: str, package_id: str, zip_path: str) -> None:
    _log(job_id, "POLICY_PACKAGE_EXPORTED", {"package_id": package_id, "zip_path": zip_path})
