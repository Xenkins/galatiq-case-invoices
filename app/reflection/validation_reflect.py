from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.schemas.models import ReflectionResult
from app.tools.llm import reflect_stage_with_llm


def validation_reflection(
    state,
    *,
    llm_client: Any,
    grok_model: str = "grok-3",
) -> ReflectionResult:
    invoice = state.invoice_data
    validation = state.validation_result
    if invoice is None or validation is None:
        return ReflectionResult(
            status="fail",
            feedback="Validation missing required inputs/outputs.",
            confidence=0.0,
            checks=["missing_validation_output"],
        )

    checks = []
    checked_items = {str(x.get("item")) for x in validation.item_checks}
    invoice_items = {item.item for item in invoice.items}
    if not invoice_items.issubset(checked_items):
        if state.retry_counts["validate"] < 1:
            return ReflectionResult(
                status="retry",
                feedback="Not all invoice items received validation checks.",
                confidence=0.5,
                checks=["item_coverage_incomplete"],
            )
        return ReflectionResult(
            status="fail",
            feedback="Validation still missing item-level checks after retry.",
            confidence=0.2,
            checks=["item_coverage_failed"],
        )
    checks.append("item_coverage_complete")

    if not validation.validation_pass:
        checks.append("validation_fail_acknowledged")
    else:
        checks.append("validation_passed")

    reflection = ReflectionResult(
        status="pass",
        feedback="Validation output is coherent and complete.",
        confidence=0.88,
        checks=checks,
    )
    if llm_client is None:
        raise ValueError("LLM client is required for validation reflection.")
    llm_eval = reflect_stage_with_llm(
        llm_client,
        model=grok_model,
        stage="validation",
        checklist=[
            "All invoice items were evaluated.",
            "Validation outcomes align with inventory constraints.",
            "Ambiguities are routed to human review.",
        ],
        stage_input=asdict(invoice),
        stage_output=asdict(validation),
    )
    status = str(llm_eval.get("status", "pass")).lower()
    feedback = str(llm_eval.get("feedback", "")).strip()
    confidence = float(llm_eval.get("confidence", reflection.confidence))
    llm_checks = llm_eval.get("checks") if isinstance(llm_eval.get("checks"), list) else []
    if status in {"retry", "fail"}:
        return ReflectionResult(
            status=status,
            feedback=feedback or reflection.feedback,
            confidence=confidence,
            checks=reflection.checks + [str(c) for c in llm_checks],
        )
    if feedback:
        reflection.feedback = feedback
    reflection.confidence = confidence
    reflection.checks.extend([str(c) for c in llm_checks])
    return reflection
