from app.agents.validation import validation_agent
from app.schemas.models import InvoiceData, InvoiceItem, PipelineState
from app.tools.db import ensure_inventory_db


def _base_invoice() -> InvoiceData:
    return InvoiceData(
        invoice_id="INV-TEST",
        vendor="VendorCo",
        date="2026-01-01",
        due_date="2026-01-15",
        amount=100.0,
        payment_terms="Net 14",
        items=[InvoiceItem(item="WidgetA", quantity=1, unit_price=100.0, line_total=100.0)],
    )


def test_validation_flags_relative_due_date_language(tmp_path):
    db_path = tmp_path / "inventory.test.db"
    ensure_inventory_db(str(db_path))

    invoice = _base_invoice()
    invoice.due_date = None
    invoice.due_date_raw = "yesterday"
    invoice.payment_terms = "Immediate"

    state = PipelineState(invoice_path="tests/fixtures/relative_due.txt", invoice_data=invoice)
    result = validation_agent(state, str(db_path))

    issue_codes = {issue.code for issue in result.issues}
    assert "VAL_DUE_DATE_RELATIVE_LANGUAGE" in issue_codes
    assert result.validation_result is not None
    assert result.validation_result.requires_human_review is True


def test_validation_flags_net_terms_due_date_mismatch(tmp_path):
    db_path = tmp_path / "inventory.test.db"
    ensure_inventory_db(str(db_path))

    invoice = _base_invoice()
    invoice.date = "2026-01-01"
    invoice.due_date = "2026-01-30"
    invoice.due_date_raw = "2026-01-30"
    invoice.payment_terms = "Net 5"

    state = PipelineState(invoice_path="tests/fixtures/net_terms_mismatch.txt", invoice_data=invoice)
    result = validation_agent(state, str(db_path))

    issue_codes = {issue.code for issue in result.issues}
    assert "VAL_TERMS_DUE_MISMATCH" in issue_codes
    assert result.validation_result is not None
    assert result.validation_result.requires_human_review is True
