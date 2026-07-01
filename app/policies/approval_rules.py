from __future__ import annotations

from typing import List, Tuple

from app.schemas.models import (
    DECISION_APPROVE,
    DECISION_HUMAN_REVIEW,
    DECISION_REJECT,
    SEVERITY_CRITICAL,
    SEVERITY_ERROR,
    PipelineState,
)


def evaluate_approval_policy(
    state: PipelineState, vp_threshold: float = 10000.0
) -> Tuple[str, List[str], str]:
    flags: List[str] = []
    invoice = state.invoice_data
    validation = state.validation_result

    if not invoice or not validation:
        return DECISION_REJECT, ["missing_stage_output"], "Missing prior stage outputs."

    severities = {issue.severity for issue in state.issues}
    if SEVERITY_CRITICAL in severities:
        flags.append("critical_issue_present")
        return DECISION_REJECT, flags, "Rejected due to critical validation or integrity issue."

    if SEVERITY_ERROR in severities:
        flags.append("error_issue_present")
        return DECISION_HUMAN_REVIEW, flags, "Manual review required due to validation errors."

    if not validation.validation_pass:
        flags.append("validation_not_passed")
        return DECISION_HUMAN_REVIEW, flags, "Validation did not fully pass."

    if validation.requires_human_review or state.needs_human_review:
        flags.append("uncertainty_requires_review")
        return DECISION_HUMAN_REVIEW, flags, "Ambiguous or uncertain match requires manual approval."

    if (invoice.amount or 0.0) > vp_threshold:
        flags.append("vp_threshold_exceeded")
        return DECISION_HUMAN_REVIEW, flags, "Invoice exceeds VP threshold and requires escalation."

    return DECISION_APPROVE, flags, "All checks passed with no policy escalations."
