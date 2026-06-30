from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.schemas.models import InvoiceItem, Issue, PipelineState, SEVERITY_WARNING
from app.tools.file_parsers import normalize_item_name, parse_invoice_file
from app.tools.llm import extract_invoice_with_llm


def _safe_float(value: Any):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any):
    if value is None:
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _apply_llm_extraction(invoice, llm_payload: Dict[str, Any]) -> None:
    invoice_id = str(llm_payload.get("invoice_id") or "").strip()
    if invoice_id and not invoice.invoice_id:
        invoice.invoice_id = invoice_id

    vendor = str(llm_payload.get("vendor") or "").strip()
    if vendor and not invoice.vendor:
        invoice.vendor = vendor

    date = str(llm_payload.get("date") or "").strip()
    if date and not invoice.date:
        invoice.date = date

    due_date = str(llm_payload.get("due_date") or "").strip()
    if due_date and not invoice.due_date:
        invoice.due_date = due_date

    amount = _safe_float(llm_payload.get("amount"))
    if amount is not None and invoice.amount is None:
        invoice.amount = amount

    if invoice.items:
        return

    raw_items = llm_payload.get("items")
    if not isinstance(raw_items, list):
        return
    parsed_items: List[InvoiceItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        raw_item = str(entry.get("item") or "").strip()
        if not raw_item:
            continue
        qty = _safe_int(entry.get("quantity")) or 0
        unit_price = _safe_float(entry.get("unit_price"))
        line_total = _safe_float(entry.get("line_total"))
        if line_total is None and unit_price is not None:
            line_total = qty * unit_price
        parsed_items.append(
            InvoiceItem(
                item=normalize_item_name(raw_item),
                raw_item=raw_item,
                quantity=qty,
                unit_price=unit_price,
                line_total=line_total,
            )
        )
    if parsed_items:
        invoice.items = parsed_items


def ingestion_agent(
    state: PipelineState,
    *,
    llm_client: Any,
    grok_model: str = "grok-3",
) -> PipelineState:
    invoice = parse_invoice_file(state.invoice_path)
    invoice_path = Path(state.invoice_path)
    used_llm = False

    if llm_client is None:
        raise ValueError("LLM client is required for ingestion.")
    if not invoice.raw_text.strip():
        raise ValueError("Invoice raw text is empty; cannot run LLM ingestion.")

    llm_payload = extract_invoice_with_llm(
        llm_client,
        model=grok_model,
        raw_text=invoice.raw_text,
        source_type=invoice.source_type,
    )
    _apply_llm_extraction(invoice, llm_payload)
    used_llm = True

    if not invoice.invoice_id:
        invoice.invoice_id = invoice_path.stem.replace("_", "-").upper()
        state.add_issue(
            Issue(
                code="INGEST_INFERRED_INVOICE_ID",
                severity=SEVERITY_WARNING,
                message="Invoice ID missing; inferred from filename.",
                field="invoice_id",
                stage="ingestion",
            )
        )
    if not invoice.vendor:
        state.add_issue(
            Issue(
                code="INGEST_MISSING_VENDOR",
                severity=SEVERITY_WARNING,
                message="Vendor missing from extracted invoice data.",
                field="vendor",
                stage="ingestion",
            )
        )
    if invoice.amount is None and invoice.items:
        invoice.amount = sum((item.line_total or 0.0) for item in invoice.items)
        state.add_issue(
            Issue(
                code="INGEST_INFERRED_TOTAL",
                severity=SEVERITY_WARNING,
                message="Invoice total was inferred from line items.",
                field="amount",
                stage="ingestion",
            )
        )

    state.invoice_data = invoice
    state.log_stage(
        "ingestion",
        "Parsed invoice document into normalized schema.",
        {
            "invoice_id": invoice.invoice_id,
            "vendor": invoice.vendor,
            "item_count": len(invoice.items),
            "amount": invoice.amount,
            "source_type": invoice.source_type,
            "llm_used": used_llm,
        },
    )
    return state
