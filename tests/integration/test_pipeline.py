from pathlib import Path

from app.graph.builder import run_invoice_pipeline


def test_pipeline_happy_path_txt():
    root = Path(__file__).resolve().parents[2]
    invoice_path = root / "data" / "invoices" / "invoice_1001.txt"
    db_path = root / "inventory.test.db"
    result = run_invoice_pipeline(str(invoice_path), str(db_path))
    assert result["final_status"] in {"APPROVED_PAID", "HUMAN_REVIEW_REQUIRED", "REJECTED"}
    assert result["invoice_data"]["invoice_id"]


def test_pipeline_catches_invalid_quantity():
    root = Path(__file__).resolve().parents[2]
    invoice_path = root / "data" / "invoices" / "invoice_1009.json"
    db_path = root / "inventory.test.db"
    result = run_invoice_pipeline(str(invoice_path), str(db_path))
    assert result["final_status"] in {"REJECTED", "HUMAN_REVIEW_REQUIRED", "FAILED"}
    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "VAL_INVALID_QUANTITY" in issue_codes
