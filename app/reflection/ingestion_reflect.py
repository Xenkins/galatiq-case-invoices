from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.schemas.models import ReflectionResult
from app.tools.llm import reflect_stage_with_llm


def ingest_reflection(state, *, llm_client: Any, grok_model: str = "grok-3") -> ReflectionResult:
    invoice = state.invoice_data
    if invoice is None:
        return ReflectionResult(
            status="fail",
            feedback="Ingestion did not produce invoice_data.",
            confidence=0.0,
            checks=["missing_invoice_data"],
        )

    checks = []
    missing = []
    if not invoice.invoice_id:
        missing.append("invoice_id")
    if invoice.amount is None:
        missing.append("amount")
    if not invoice.items:
        missing.append("items")

    if missing and state.retry_counts["ingest"] < 1:
        return ReflectionResult(
            status="retry",
            feedback=f"Missing required fields: {', '.join(missing)}",
            confidence=0.45,
            checks=["required_fields"],
        )

    if missing:
        checks.append("missing_fields_persisted")
        return ReflectionResult(
            status="fail",
            feedback=f"Missing required fields after retry: {', '.join(missing)}",
            confidence=0.2,
            checks=checks,
        )

    checks.append("required_fields_present")
    if invoice.due_date is None:
        checks.append("due_date_missing_non_blocking")
    result = ReflectionResult(
        status="pass",
        feedback="Ingestion output meets minimum schema quality checks.",
        confidence=0.9,
        checks=checks,
    )
    if llm_client is None:
        raise ValueError("LLM client is required for ingestion reflection.")
    llm_eval = reflect_stage_with_llm(
        llm_client,
        model=grok_model,
        stage="ingestion",
        checklist=[
            "Required invoice fields are present.",
            "Dates and amounts are parseable and plausible.",
            "Line items are structured for downstream validation.",
        ],
        stage_input={"invoice_path": state.invoice_path},
        stage_output=asdict(invoice),
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
