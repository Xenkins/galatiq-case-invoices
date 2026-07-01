from __future__ import annotations

import re
from typing import Any

from app.policies.approval_rules import evaluate_approval_policy
from app.schemas.models import (
    ApprovalResult,
    DECISION_APPROVE,
    DECISION_HUMAN_REVIEW,
    PipelineState,
)
from app.tools.llm import generate_approval_rationale_with_llm


def _payment_context_flags(invoice) -> list[str]:
    flags: list[str] = []
    text = " ".join(
        [
            (invoice.payment_terms or ""),
            (invoice.notes or ""),
            (invoice.due_date_raw or ""),
        ]
    ).lower()
    if not text.strip():
        return flags

    if any(term in text for term in ["urgent", "avoid penalties", "immediately", "asap"]):
        flags.append("payment_pressure_language_detected")

    if any(term in text for term in ["wire transfer", "bank account", "new account", "crypto"]):
        flags.append("nonstandard_payment_instruction")

    if invoice.payment_terms and re.search(r"net\s*\d{1,3}", invoice.payment_terms, re.IGNORECASE):
        if invoice.notes and any(term in invoice.notes.lower() for term in ["immediately", "asap", "urgent"]):
            flags.append("payment_terms_note_conflict")

    return flags


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
        context_flags = _payment_context_flags(invoice)
        for flag in context_flags:
            if flag not in flags:
                flags.append(flag)
        if context_flags and decision == DECISION_APPROVE:
            decision = DECISION_HUMAN_REVIEW
            rationale = (
                "Manual review required due to elevated payment-instruction risk context "
                "(urgent language or non-standard payment conditions)."
            )

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
                "payment_terms": invoice.payment_terms,
                "due_date": invoice.due_date,
                "due_date_raw": invoice.due_date_raw,
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
