from __future__ import annotations

from app.schemas.models import PaymentResult, PipelineState
from app.tools.payment import mock_payment


def payment_agent(state: PipelineState) -> PipelineState:
    invoice = state.invoice_data
    approval = state.approval_result
    if invoice is None or approval is None:
        raise ValueError("Payment agent requires invoice and approval results.")

    if approval.decision != "APPROVE":
        state.payment_result = PaymentResult(
            status="skipped",
            message=f"Payment skipped because decision is {approval.decision}.",
            transaction_id=None,
        )
        state.log_stage(
            "payment",
            "Skipped payment due to non-approved decision.",
            {"decision": approval.decision},
        )
        return state

    payment = mock_payment(invoice.vendor, invoice.amount or 0.0)
    state.payment_result = PaymentResult(
        status=payment["status"],
        message=payment["message"],
        transaction_id=payment["transaction_id"],
    )
    state.log_stage(
        "payment",
        "Executed mock payment for approved invoice.",
        {"transaction_id": state.payment_result.transaction_id},
    )
    return state
