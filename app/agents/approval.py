from __future__ import annotations

from typing import Any

from app.policies.approval_rules import evaluate_approval_policy
from app.schemas.models import ApprovalResult, PipelineState
from app.tools.llm import generate_approval_rationale_with_llm


def approval_agent(
    state: PipelineState,
    vp_threshold: float = 10000.0,
    *,
    llm_client: Any,
    grok_model: str = "grok-3",
) -> PipelineState:
    decision, flags, rationale = evaluate_approval_policy(state, vp_threshold=vp_threshold)
    if llm_client is None:
        raise ValueError("LLM client is required for approval.")
    if state.invoice_data is not None:
        invoice = state.invoice_data
        issue_payload = [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "stage": issue.stage,
            }
            for issue in state.issues
        ]
        llm_rationale = generate_approval_rationale_with_llm(
            llm_client,
            model=grok_model,
            decision=decision,
            policy_flags=flags,
            invoice_summary={
                "invoice_id": invoice.invoice_id,
                "vendor": invoice.vendor,
                "amount": invoice.amount,
                "item_count": len(invoice.items),
            },
            issues=issue_payload,
        )
        if llm_rationale:
            rationale = llm_rationale

    result = ApprovalResult(
        decision=decision,
        rationale=rationale,
        requires_human_review=(decision != "APPROVE"),
        policy_flags=flags,
    )
    state.approval_result = result
    if result.requires_human_review:
        state.needs_human_review = True
    state.log_stage(
        "approval",
        "Applied approval policy to validation result.",
        {"decision": result.decision, "policy_flags": result.policy_flags},
    )
    return state
