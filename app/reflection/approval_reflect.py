from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.schemas.models import ReflectionResult
from app.tools.llm import reflect_stage_with_llm


def approval_reflection(
    state,
    *,
    llm_client: Any,
    grok_model: str = "grok-3",
) -> ReflectionResult:
    approval = state.approval_result
    validation = state.validation_result
    if approval is None or validation is None:
        return ReflectionResult(
            status="fail",
            feedback="Approval reflection missing prior outputs.",
            confidence=0.0,
            checks=["missing_approval_output"],
        )

    checks = []
    if approval.decision == "APPROVE" and not validation.validation_pass:
        if state.retry_counts["approve"] < 1:
            return ReflectionResult(
                status="retry",
                feedback="Approval cannot be APPROVE while validation has not passed.",
                confidence=0.4,
                checks=["decision_policy_conflict"],
            )
        return ReflectionResult(
            status="fail",
            feedback="Approval policy conflict persisted after retry.",
            confidence=0.1,
            checks=["decision_policy_conflict_persisted"],
        )

    if not approval.rationale.strip():
        return ReflectionResult(
            status="retry" if state.retry_counts["approve"] < 1 else "fail",
            feedback="Approval rationale is empty; provide explicit explanation.",
            confidence=0.3,
            checks=["missing_rationale"],
        )

    checks.append("policy_consistent")
    checks.append("rationale_present")
    result = ReflectionResult(
        status="pass",
        feedback="Approval decision is consistent with policy constraints.",
        confidence=0.91,
        checks=checks,
    )
    if llm_client is None:
        raise ValueError("LLM client is required for approval reflection.")
    llm_eval = reflect_stage_with_llm(
        llm_client,
        model=grok_model,
        stage="approval",
        checklist=[
            "Decision follows policy and validation outputs.",
            "Rationale is explicit and auditable.",
            "Human-review gating is applied for uncertainty.",
        ],
        stage_input=asdict(state.validation_result),
        stage_output=asdict(approval),
    )
    status = str(llm_eval.get("status", "pass")).lower()
    feedback = str(llm_eval.get("feedback", "")).strip()
    confidence = float(llm_eval.get("confidence", result.confidence))
    llm_checks = llm_eval.get("checks") if isinstance(llm_eval.get("checks"), list) else []
    if status in {"retry", "fail"}:
        return ReflectionResult(
            status=status,
            feedback=feedback or result.feedback,
            confidence=confidence,
            checks=result.checks + [str(c) for c in llm_checks],
        )
    if feedback:
        result.feedback = feedback
    result.confidence = confidence
    result.checks.extend([str(c) for c in llm_checks])
    return result
