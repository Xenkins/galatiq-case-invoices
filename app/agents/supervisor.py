from __future__ import annotations

from app.schemas.models import Issue, PipelineState, SEVERITY_ERROR


def supervisor_agent(state: PipelineState) -> PipelineState:
    approval = state.approval_result
    payment = state.payment_result

    if approval is None:
        state.final_status = "FAILED"
        state.log_stage("supervisor", "Missing approval result.", {})
        return state

    if approval.decision == "APPROVE":
        if payment is None or payment.status != "success":
            state.add_issue(
                Issue(
                    code="SUP_PAYMENT_MISSING",
                    severity=SEVERITY_ERROR,
                    message="Approved invoice has no successful payment record.",
                    stage="supervisor",
                )
            )
            state.final_status = "FAILED"
        else:
            state.final_status = "APPROVED_PAID"
    elif approval.decision == "HUMAN_REVIEW":
        state.final_status = "HUMAN_REVIEW_REQUIRED"
    else:
        state.final_status = "REJECTED"

    state.log_stage(
        "supervisor",
        "Ran final consistency checks and set pipeline status.",
        {"final_status": state.final_status},
    )
    return state
