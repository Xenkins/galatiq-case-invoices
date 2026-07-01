from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import re
from typing import Dict, List

from app.schemas.models import (
    Issue,
    MatchCandidate,
    PipelineState,
    SEVERITY_CRITICAL,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    ValidationResult,
)
from app.tools.db import fetch_inventory
from app.tools.fuzzy_match import rank_candidates
from app.tools.file_parsers import normalize_item_name


RELATIVE_DUE_LANGUAGE = {
    "yesterday",
    "today",
    "tomorrow",
    "immediate",
    "immediately",
    "asap",
    "upon receipt",
    "next week",
    "next month",
}


def _is_relative_due_date(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in RELATIVE_DUE_LANGUAGE)


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _extract_net_days(payment_terms: str | None) -> int | None:
    if not payment_terms:
        return None
    match = re.search(r"net\s*(\d{1,3})", payment_terms, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def validation_agent(state: PipelineState, db_path: str, fuzzy_threshold: float = 0.85) -> PipelineState:
    invoice = state.invoice_data
    if invoice is None:
        raise ValueError("Validation agent requires invoice_data.")

    inventory = fetch_inventory(db_path)
    canonical_inventory = {normalize_item_name(k): (k, v) for k, v in inventory.items()}

    item_checks: List[Dict[str, object]] = []
    fuzzy_candidates: Dict[str, List[MatchCandidate]] = {}
    requires_human_review = False
    validation_pass = True

    aggregate_qty: Dict[str, int] = defaultdict(int)
    for item in invoice.items:
        aggregate_qty[item.item] += item.quantity

    for item_name, qty in aggregate_qty.items():
        check: Dict[str, object] = {"item": item_name, "quantity": qty}

        if qty <= 0:
            validation_pass = False
            check["status"] = "invalid_quantity"
            state.add_issue(
                Issue(
                    code="VAL_INVALID_QUANTITY",
                    severity=SEVERITY_CRITICAL,
                    message=f"Invalid quantity {qty} for item {item_name}.",
                    field="quantity",
                    stage="validation",
                )
            )
            item_checks.append(check)
            continue

        matched = canonical_inventory.get(item_name)
        if matched is None:
            validation_pass = False
            choices = rank_candidates(item_name, canonical_inventory.keys(), top_k=3)
            top_conf = choices[0].confidence if choices else 0.0
            if choices and top_conf >= fuzzy_threshold:
                fuzzy_candidates[item_name] = choices
                requires_human_review = True
                check["status"] = "ambiguous_fuzzy_match"
                check["top_confidence"] = top_conf
                state.add_issue(
                    Issue(
                        code="VAL_AMBIGUOUS_ITEM_MATCH",
                        severity=SEVERITY_WARNING,
                        message=f"Item {item_name} has no exact match; manual mapping required.",
                        field="item",
                        stage="validation",
                        details={"candidates": [c.candidate for c in choices]},
                    )
                )
            else:
                check["status"] = "unknown_item"
                state.add_issue(
                    Issue(
                        code="VAL_UNKNOWN_ITEM",
                        severity=SEVERITY_ERROR,
                        message=f"Item {item_name} not found in inventory.",
                        field="item",
                        stage="validation",
                    )
                )
            item_checks.append(check)
            continue

        original_key, stock = matched
        check["matched_inventory_item"] = original_key
        check["stock"] = stock

        if stock <= 0:
            validation_pass = False
            check["status"] = "out_of_stock"
            state.add_issue(
                Issue(
                    code="VAL_OUT_OF_STOCK",
                    severity=SEVERITY_CRITICAL,
                    message=f"Item {original_key} has zero available stock.",
                    field="item",
                    stage="validation",
                )
            )
        elif qty > stock:
            validation_pass = False
            check["status"] = "stock_mismatch"
            state.add_issue(
                Issue(
                    code="VAL_STOCK_MISMATCH",
                    severity=SEVERITY_ERROR,
                    message=f"Requested quantity {qty} exceeds stock {stock} for {original_key}.",
                    field="quantity",
                    stage="validation",
                )
            )
        else:
            check["status"] = "ok"

        item_checks.append(check)

    computed_total = sum((item.line_total or 0.0) for item in invoice.items)
    totals_check = {
        "invoice_amount": invoice.amount,
        "computed_line_total": round(computed_total, 2),
        "difference": round((invoice.amount or computed_total) - computed_total, 2),
    }
    if (
        invoice.amount is not None
        and (
            (invoice.amount + 1.0) < computed_total
            or (computed_total > 0 and invoice.amount > computed_total * 1.25)
        )
    ):
        requires_human_review = True
        state.add_issue(
            Issue(
                code="VAL_TOTAL_MISMATCH",
                severity=SEVERITY_WARNING,
                message="Invoice total differs from computed line item total.",
                field="amount",
                stage="validation",
                details=totals_check,
            )
        )

    due_raw = (invoice.due_date_raw or "").strip()
    if due_raw:
        if _is_relative_due_date(due_raw):
            requires_human_review = True
            state.add_issue(
                Issue(
                    code="VAL_DUE_DATE_RELATIVE_LANGUAGE",
                    severity=SEVERITY_WARNING,
                    message=f"Due date uses relative language ({due_raw!r}) and requires manual interpretation.",
                    field="due_date",
                    stage="validation",
                )
            )
        elif invoice.due_date is None:
            requires_human_review = True
            state.add_issue(
                Issue(
                    code="VAL_DUE_DATE_UNPARSABLE",
                    severity=SEVERITY_WARNING,
                    message=f"Due date value ({due_raw!r}) could not be normalized.",
                    field="due_date",
                    stage="validation",
                )
            )

    invoice_date = _parse_iso_date(invoice.date)
    due_date = _parse_iso_date(invoice.due_date)
    if invoice_date and due_date and due_date < invoice_date:
        requires_human_review = True
        state.add_issue(
            Issue(
                code="VAL_DUE_BEFORE_INVOICE_DATE",
                severity=SEVERITY_ERROR,
                message=f"Due date {due_date.isoformat()} is before invoice date {invoice_date.isoformat()}.",
                field="due_date",
                stage="validation",
            )
        )

    payment_terms = (invoice.payment_terms or "").strip()
    net_days = _extract_net_days(payment_terms)
    if net_days is not None:
        if invoice_date and due_date:
            expected_due = invoice_date + timedelta(days=net_days)
            if abs((due_date - expected_due).days) > 2:
                requires_human_review = True
                state.add_issue(
                    Issue(
                        code="VAL_TERMS_DUE_MISMATCH",
                        severity=SEVERITY_WARNING,
                        message=(
                            f"Payment terms {payment_terms!r} imply due date near {expected_due.isoformat()}, "
                            f"but invoice states {due_date.isoformat()}."
                        ),
                        field="payment_terms",
                        stage="validation",
                    )
                )
        elif invoice_date and not due_date:
            requires_human_review = True
            state.add_issue(
                Issue(
                    code="VAL_TERMS_WITHOUT_PARSEABLE_DUE_DATE",
                    severity=SEVERITY_WARNING,
                    message=f"Payment terms {payment_terms!r} present but due date is missing/unparseable.",
                    field="payment_terms",
                    stage="validation",
                )
            )

    immediate_terms = {"immediate", "due on receipt", "upon receipt", "cod"}
    if payment_terms and any(term in payment_terms.lower() for term in immediate_terms):
        if invoice_date and due_date and (due_date - invoice_date).days > 1:
            requires_human_review = True
            state.add_issue(
                Issue(
                    code="VAL_IMMEDIATE_TERMS_INCONSISTENT_DUE_DATE",
                    severity=SEVERITY_WARNING,
                    message=(
                        f"Payment terms {payment_terms!r} suggest immediate payment, "
                        f"but due date is {due_date.isoformat()}."
                    ),
                    field="payment_terms",
                    stage="validation",
                )
            )

    result = ValidationResult(
        validation_pass=validation_pass,
        requires_human_review=requires_human_review,
        item_checks=item_checks,
        fuzzy_candidates=fuzzy_candidates,
        totals_check=totals_check,
    )
    state.validation_result = result
    state.needs_human_review = state.needs_human_review or requires_human_review
    state.log_stage(
        "validation",
        "Validated invoice against inventory database.",
        {
            "validation_pass": validation_pass,
            "requires_human_review": requires_human_review,
            "issue_count": len(state.issues),
        },
    )
    return state
