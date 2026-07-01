from __future__ import annotations

import csv
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.graph.builder import pretty_result, run_invoice_pipeline
from app.tools.payment import mock_payment


class RunRequest(BaseModel):
    invoice_path: str = Field(..., description="Path to the invoice file.")
    db_path: str = Field(default="inventory.db")
    vp_threshold: float = Field(default=10000.0)
    grok_model: str = Field(default_factory=lambda: os.getenv("GROK_MODEL", "grok-3"))


class RunStatus(BaseModel):
    run_id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)


class ManualReviewRequest(BaseModel):
    action: str = Field(..., description="approve_and_pay or reject")
    reviewer: str = Field(..., description="Reviewer name or identifier")
    reason: str = Field(..., description="Manual override rationale")


app = FastAPI(title="Galatiq Invoice Orchestration API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS: Dict[str, RunStatus] = {}
RUNS_LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parents[1]
UI_DIST = ROOT / "ui" / "dist"
UPLOAD_DIR = ROOT / "data" / "uploads"
ALLOWED_UPLOAD_SUFFIXES = {".txt", ".json", ".csv", ".xml", ".pdf"}
load_dotenv(dotenv_path=ROOT / ".env")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")


def _append_event(run_id: str, event: Dict[str, Any]) -> None:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            return
        run.events.append(event)


def _resolve_allowed_source(path: str) -> Path:
    source_path = Path(path)
    source_abs = (ROOT / source_path).resolve() if not source_path.is_absolute() else source_path.resolve()
    allowed_roots = [(ROOT / "data" / "invoices").resolve(), UPLOAD_DIR.resolve()]
    if not any(root in source_abs.parents or source_abs == root for root in allowed_roots):
        raise HTTPException(status_code=400, detail="Requested path is outside allowed invoice directories.")
    if not source_abs.exists() or not source_abs.is_file():
        raise HTTPException(status_code=404, detail="Source file not found.")
    if source_abs.suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported source file type.")
    return source_abs


def _preview_lines_for_source(path: Path) -> List[str]:
    def _table_lines(rows: List[List[str]]) -> List[str]:
        if not rows:
            return []
        col_count = max(len(row) for row in rows)
        widths = [0] * col_count
        for row in rows:
            for idx in range(col_count):
                value = row[idx] if idx < len(row) else ""
                widths[idx] = min(max(widths[idx], len(value)), 30)

        lines: List[str] = []
        for row_idx, row in enumerate(rows):
            padded = []
            for idx in range(col_count):
                value = row[idx] if idx < len(row) else ""
                padded.append(value[: widths[idx]].ljust(widths[idx]))
            lines.append(" | ".join(padded).rstrip())
            if row_idx == 0:
                lines.append("-+-".join("-" * widths[idx] for idx in range(col_count)))
        return lines

    def _json_scalar(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _json_to_lines(parsed: Any) -> List[str]:
        if isinstance(parsed, list):
            if parsed and all(isinstance(item, dict) for item in parsed):
                keys: List[str] = []
                for item in parsed:
                    for key in item.keys():
                        if key not in keys:
                            keys.append(str(key))
                rows = [keys]
                for item in parsed:
                    rows.append([_json_scalar(item.get(key)) for key in keys])
                return _table_lines(rows)
            return json.dumps(parsed, indent=2, ensure_ascii=False).splitlines()

        if isinstance(parsed, dict):
            lines: List[str] = []
            scalar_rows: List[List[str]] = [["Field", "Value"]]
            list_sections: List[tuple[str, Any]] = []
            object_sections: List[tuple[str, Any]] = []

            for key, value in parsed.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    scalar_rows.append([str(key), _json_scalar(value)])
                elif isinstance(value, list):
                    list_sections.append((str(key), value))
                elif isinstance(value, dict):
                    object_sections.append((str(key), value))
                else:
                    scalar_rows.append([str(key), _json_scalar(value)])

            if len(scalar_rows) > 1:
                lines.extend(_table_lines(scalar_rows))

            for key, value in object_sections:
                if lines:
                    lines.append("")
                lines.append(f"{key}:")
                nested = json.dumps(value, indent=2, ensure_ascii=False).splitlines()
                lines.extend([f"  {line}" for line in nested])

            for key, value in list_sections:
                if lines:
                    lines.append("")
                lines.append(f"{key}:")
                if value and all(isinstance(item, dict) for item in value):
                    keys: List[str] = []
                    for item in value:
                        for item_key in item.keys():
                            if item_key not in keys:
                                keys.append(str(item_key))
                    rows = [keys]
                    for item in value:
                        rows.append([_json_scalar(item.get(item_key)) for item_key in keys])
                    lines.extend(_table_lines(rows))
                else:
                    nested = json.dumps(value, indent=2, ensure_ascii=False).splitlines()
                    lines.extend([f"  {line}" for line in nested])

            return lines or ["(empty object)"]

        return json.dumps(parsed, indent=2, ensure_ascii=False).splitlines()

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".txt":
        return text.splitlines()
    if suffix == ".json":
        try:
            parsed = json.loads(text)
            return _json_to_lines(parsed)
        except Exception:
            return text.splitlines()
    if suffix == ".csv":
        try:
            rows = list(csv.reader(text.splitlines()))
            if not rows:
                return ["(empty csv)"]
            if (
                len(rows[0]) >= 2
                and rows[0][0].strip().lower() == "field"
                and rows[0][1].strip().lower() == "value"
            ):
                pairs = []
                for row in rows[1:]:
                    if not row:
                        continue
                    key = (row[0] if len(row) > 0 else "").strip()
                    value = (row[1] if len(row) > 1 else "").strip()
                    if key:
                        pairs.append((key, value))

                item_key_map = {
                    "item": "Item",
                    "qty": "Qty",
                    "quantity": "Qty",
                    "unit_price": "Unit Price",
                    "unit price": "Unit Price",
                    "line_total": "Line Total",
                    "line total": "Line Total",
                }
                metadata_rows: List[List[str]] = [["Field", "Value"]]
                line_items: List[Dict[str, str]] = []
                current_item: Dict[str, str] = {}

                for raw_key, raw_value in pairs:
                    key_lower = raw_key.strip().lower()
                    mapped = item_key_map.get(key_lower)
                    if mapped:
                        if mapped == "Item" and current_item:
                            line_items.append(current_item)
                            current_item = {}
                        current_item[mapped] = raw_value
                    else:
                        metadata_rows.append([raw_key, raw_value])
                if current_item:
                    line_items.append(current_item)

                lines: List[str] = _table_lines(metadata_rows)
                if line_items:
                    lines.append("")
                    lines.append("Line Items:")
                    item_rows: List[List[str]] = [["Item", "Qty", "Unit Price", "Line Total"]]
                    for item in line_items:
                        item_rows.append(
                            [
                                item.get("Item", ""),
                                item.get("Qty", ""),
                                item.get("Unit Price", ""),
                                item.get("Line Total", ""),
                            ]
                        )
                    lines.extend(_table_lines(item_rows))
                return lines
            return _table_lines(rows)
        except Exception:
            return text.splitlines()
    if suffix == ".xml":
        return text.splitlines() if text.splitlines() else ["(empty xml)"]
    return ["Preview unavailable for selected file."]


def _parse_csv_rows(text: str) -> List[List[str]]:
    return list(csv.reader(text.splitlines()))


def _is_field_value_csv(rows: List[List[str]]) -> bool:
    return bool(
        rows
        and len(rows[0]) >= 2
        and rows[0][0].strip().lower() == "field"
        and rows[0][1].strip().lower() == "value"
    )


def _build_preview_pdf_from_csv_rows(rows: List[List[str]]) -> bytes:
    try:
        from fpdf import FPDF
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF preview dependency unavailable: {exc}") from exc

    if not rows:
        return _build_preview_pdf_bytes(["(empty csv)"])

    col_count = max(len(row) for row in rows)
    orientation = "L" if col_count >= 7 else "P"
    pdf = FPDF(orientation=orientation)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_font("Helvetica", "", 8)

    def value_at(row: List[str], idx: int) -> str:
        return (row[idx] if idx < len(row) else "").strip()

    # Estimate column widths using visible content and then fit to page width.
    col_widths: List[float] = []
    for idx in range(col_count):
        max_chars = 0
        for row in rows[:120]:
            max_chars = max(max_chars, len(value_at(row, idx)))
        max_chars = min(max_chars, 28)
        candidate = max(pdf.get_string_width("M" * max_chars) + 4, 18.0)
        col_widths.append(min(candidate, 62.0))

    effective_width = pdf.w - pdf.l_margin - pdf.r_margin
    total_width = sum(col_widths)
    if total_width > effective_width:
        scale = effective_width / total_width
        col_widths = [max(12.0, width * scale) for width in col_widths]

    line_h = 5.2
    bottom_limit = pdf.h - 12

    def fit_text(text: str, width: float) -> str:
        out = text
        while out and pdf.get_string_width(out) > max(width - 2, 2):
            if len(out) <= 1:
                break
            out = out[:-1]
        if out != text and len(out) >= 1:
            while out and pdf.get_string_width(out + "...") > max(width - 2, 2):
                out = out[:-1]
            return f"{out}..."
        return out

    def draw_header() -> None:
        header = rows[0]
        pdf.set_font("Helvetica", "B", 8)
        for idx in range(col_count):
            label = fit_text(value_at(header, idx), col_widths[idx])
            pdf.cell(col_widths[idx], line_h, label, border=1)
        pdf.ln(line_h)
        pdf.set_font("Helvetica", "", 8)

    draw_header()
    for row in rows[1:]:
        if pdf.get_y() + line_h > bottom_limit:
            pdf.add_page()
            draw_header()
        for idx in range(col_count):
            value = fit_text(value_at(row, idx), col_widths[idx])
            pdf.cell(col_widths[idx], line_h, value, border=1)
        pdf.ln(line_h)

    output = pdf.output()
    return bytes(output)


def _build_preview_pdf_from_xml_text(text: str) -> bytes:
    try:
        from fpdf import FPDF
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF preview dependency unavailable: {exc}") from exc

    root = ET.fromstring(text)

    def nice_label(tag: str) -> str:
        return tag.replace("_", " ").strip().title()

    def get_child_text(parent: Optional[ET.Element], tag: str) -> str:
        if parent is None:
            return ""
        child = parent.find(tag)
        return (child.text or "").strip() if child is not None and child.text else ""

    def draw_table(pdf: Any, rows: List[List[str]], *, line_h: float = 5.4) -> None:
        if not rows:
            return
        col_count = max(len(row) for row in rows)
        norm_rows = [[(row[i] if i < len(row) else "").strip() for i in range(col_count)] for row in rows]

        col_widths: List[float] = []
        for idx in range(col_count):
            max_chars = max((len(r[idx]) for r in norm_rows), default=0)
            max_chars = min(max_chars, 26)
            col_widths.append(max(pdf.get_string_width("M" * max_chars) + 4, 20.0))

        effective_width = pdf.w - pdf.l_margin - pdf.r_margin
        total_width = sum(col_widths)
        if total_width > effective_width:
            scale = effective_width / total_width
            col_widths = [max(16.0, w * scale) for w in col_widths]

        def fit_text(value: str, width: float) -> str:
            out = value
            while out and pdf.get_string_width(out) > max(width - 2, 2):
                out = out[:-1]
            if out != value and out:
                while out and pdf.get_string_width(out + "...") > max(width - 2, 2):
                    out = out[:-1]
                return f"{out}..."
            return out

        for row_idx, row in enumerate(norm_rows):
            if pdf.get_y() + line_h > pdf.h - 12:
                pdf.add_page()
            pdf.set_font("Helvetica", "B" if row_idx == 0 else "", 8.5)
            for idx, value in enumerate(row):
                pdf.cell(col_widths[idx], line_h, fit_text(value, col_widths[idx]), border=1)
            pdf.ln(line_h)

    header = root.find("header")
    line_items = root.findall("./line_items/item")
    totals = root.find("totals")

    header_rows = [["Field", "Value"]]
    for tag in ["invoice_number", "vendor", "date", "due_date", "currency", "payment_terms"]:
        value = get_child_text(header, tag) if tag != "payment_terms" else get_child_text(root, tag)
        if value:
            header_rows.append([nice_label(tag), value])

    item_columns: List[str] = []
    for item in line_items:
        for child in list(item):
            tag = child.tag.strip()
            if tag and tag not in item_columns:
                item_columns.append(tag)

    item_rows: List[List[str]] = []
    if item_columns:
        item_rows.append([nice_label(col) for col in item_columns])
        for item in line_items:
            item_rows.append([(get_child_text(item, col)) for col in item_columns])

    total_rows = [["Field", "Value"]]
    if totals is not None:
        for child in list(totals):
            value = (child.text or "").strip() if child.text else ""
            if value:
                total_rows.append([nice_label(child.tag), value])

    if len(header_rows) <= 1 and not item_rows and len(total_rows) <= 1:
        return _build_preview_pdf_bytes(text.splitlines() if text.splitlines() else ["(empty xml)"])

    orientation = "L" if item_rows and len(item_rows[0]) >= 6 else "P"
    pdf = FPDF(orientation=orientation)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_font("Helvetica", "B", 10)

    if len(header_rows) > 1:
        pdf.cell(0, 6, "Header", ln=True)
        draw_table(pdf, header_rows)
        pdf.ln(2)

    if item_rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Line Items", ln=True)
        draw_table(pdf, item_rows)
        pdf.ln(2)

    if len(total_rows) > 1:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Totals", ln=True)
        draw_table(pdf, total_rows)

    output = pdf.output()
    return bytes(output)


def _build_preview_pdf_bytes(lines: List[str]) -> bytes:
    try:
        from fpdf import FPDF
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF preview dependency unavailable: {exc}") from exc

    max_line_len = max((len(line) for line in lines), default=0)
    orientation = "L" if max_line_len > 110 else "P"
    font_size = 9 if max_line_len > 90 else 10
    pdf = FPDF(orientation=orientation)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_font("Courier", "", font_size)
    effective_width = pdf.w - pdf.l_margin - pdf.r_margin
    char_width = max(pdf.get_string_width("M"), 1.0)
    max_chars = max(int(effective_width // char_width), 20)
    for line in lines:
        safe_line = (line or "").replace("\t", "    ")
        while len(safe_line) > max_chars:
            pdf.cell(0, 5, safe_line[:max_chars], ln=True)
            safe_line = safe_line[max_chars:]
        pdf.cell(0, 5, safe_line, ln=True)

    output = pdf.output()
    return bytes(output)


def _execute_run(run_id: str, req: RunRequest) -> None:
    try:
        result = run_invoice_pipeline(
            invoice_path=req.invoice_path,
            db_path=req.db_path,
            vp_threshold=req.vp_threshold,
            grok_api_key=os.getenv("GROK_API_KEY"),
            grok_model=req.grok_model,
            on_event=lambda event: _append_event(run_id, event),
        )
        summary = pretty_result(result)
        with RUNS_LOCK:
            run = RUNS[run_id]
            run.status = "completed"
            run.completed_at = datetime.utcnow().isoformat() + "Z"
            run.result = result
            run.summary = summary
    except Exception as exc:
        _append_event(
            run_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "stage": "run_failed",
                "summary": "Pipeline run failed.",
                "data": {"error": str(exc)},
            },
        )
        with RUNS_LOCK:
            run = RUNS[run_id]
            run.status = "failed"
            run.completed_at = datetime.utcnow().isoformat() + "Z"
            run.error = str(exc)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/invoices")
def list_invoices() -> Dict[str, List[str]]:
    invoice_roots = [ROOT / "data" / "invoices", UPLOAD_DIR]
    files: List[str] = []
    for invoice_dir in invoice_roots:
        if not invoice_dir.exists():
            continue
        files.extend(
            str(path.relative_to(ROOT))
            for path in invoice_dir.glob("*")
            if path.is_file() and path.suffix.lower() in ALLOWED_UPLOAD_SUFFIXES
        )
    files = sorted(set(files))
    return {"invoices": files}


@app.get("/api/source")
def get_source(path: str) -> Any:
    source_abs = _resolve_allowed_source(path)
    return FileResponse(source_abs)


@app.get("/api/preview_pdf")
def get_preview_pdf(path: str) -> Any:
    source_abs = _resolve_allowed_source(path)
    if source_abs.suffix.lower() == ".pdf":
        return FileResponse(source_abs)

    sibling_pdf = source_abs.with_suffix(".pdf")
    if sibling_pdf.exists():
        return FileResponse(sibling_pdf)

    if source_abs.suffix.lower() == ".csv":
        text = source_abs.read_text(encoding="utf-8", errors="ignore")
        rows = _parse_csv_rows(text)
        if rows and not _is_field_value_csv(rows):
            pdf_bytes = _build_preview_pdf_from_csv_rows(rows)
        else:
            # Field/value CSVs are better represented by the custom line formatter.
            lines = _preview_lines_for_source(source_abs)
            pdf_bytes = _build_preview_pdf_bytes(lines)
    elif source_abs.suffix.lower() == ".xml":
        text = source_abs.read_text(encoding="utf-8", errors="ignore")
        try:
            pdf_bytes = _build_preview_pdf_from_xml_text(text)
        except Exception:
            lines = _preview_lines_for_source(source_abs)
            pdf_bytes = _build_preview_pdf_bytes(lines)
    else:
        lines = _preview_lines_for_source(source_abs)
        pdf_bytes = _build_preview_pdf_bytes(lines)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{source_abs.stem}_preview.pdf"'},
    )


@app.post("/api/upload")
async def upload_invoice(file: UploadFile = File(...)) -> Dict[str, str]:
    original_name = Path(file.filename or "").name
    if not original_name:
        raise HTTPException(status_code=400, detail="Missing uploaded filename.")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix or '<none>'}.",
        )

    stored_name = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}_{original_name}"
    destination = UPLOAD_DIR / stored_name
    content = await file.read()
    destination.write_bytes(content)
    return {"invoice_path": str(destination.relative_to(ROOT))}


@app.post("/api/runs")
def start_run(req: RunRequest) -> Dict[str, str]:
    invoice_abs = (ROOT / req.invoice_path).resolve() if not Path(req.invoice_path).is_absolute() else Path(req.invoice_path)
    if not invoice_abs.exists():
        raise HTTPException(status_code=400, detail=f"Invoice not found: {req.invoice_path}")

    run_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    run = RunStatus(run_id=run_id, status="running", started_at=now)
    with RUNS_LOCK:
        RUNS[run_id] = run

    thread = threading.Thread(target=_execute_run, args=(run_id, req), daemon=True)
    thread.start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.model_dump()


@app.post("/api/runs/{run_id}/review")
def manual_review(run_id: str, req: ManualReviewRequest) -> Dict[str, Any]:
    action = req.action.strip().lower()
    reviewer = req.reviewer.strip()
    reason = req.reason.strip()
    if action not in {"approve_and_pay", "reject"}:
        raise HTTPException(status_code=400, detail="Action must be approve_and_pay or reject.")
    if not reviewer:
        raise HTTPException(status_code=400, detail="Reviewer is required.")
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required.")

    now = datetime.utcnow().isoformat() + "Z"
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "completed":
            raise HTTPException(status_code=400, detail="Manual review is only available for completed runs.")
        if not run.result:
            raise HTTPException(status_code=400, detail="Run result is missing.")

        result = run.result
        invoice = result.get("invoice_data") or {}
        approval_result = result.get("approval_result") or {}
        policy_flags = list(approval_result.get("policy_flags") or [])
        if "manual_review_override" not in policy_flags:
            policy_flags.append("manual_review_override")

        manual_record = {
            "action": action,
            "reviewer": reviewer,
            "reason": reason,
            "timestamp": now,
            "prior_decision": approval_result.get("decision"),
            "prior_final_status": result.get("final_status"),
        }

        if action == "approve_and_pay":
            payment = mock_payment(str(invoice.get("vendor") or ""), float(invoice.get("amount") or 0.0))
            result["payment_result"] = {
                "status": payment["status"],
                "message": payment["message"],
                "transaction_id": payment["transaction_id"],
            }
            approval_result["decision"] = "APPROVE"
            approval_result["requires_human_review"] = False
            approval_result["policy_flags"] = policy_flags
            approval_result["rationale"] = (
                f"Manually approved by {reviewer}. Reason: {reason}"
            )
            result["approval_result"] = approval_result
            result["manual_review"] = manual_record
            result["needs_human_review"] = False
            result["final_status"] = "APPROVED_BY_OVERRIDE_PAID"
        else:
            result["payment_result"] = {
                "status": "skipped",
                "message": "Payment skipped after manual rejection.",
                "transaction_id": None,
            }
            approval_result["decision"] = "REJECT"
            approval_result["requires_human_review"] = False
            approval_result["policy_flags"] = policy_flags
            approval_result["rationale"] = (
                f"Manually rejected by {reviewer}. Reason: {reason}"
            )
            result["approval_result"] = approval_result
            result["manual_review"] = manual_record
            result["needs_human_review"] = False
            result["final_status"] = "REJECTED_BY_REVIEWER"

        run.result = result
        run.summary = pretty_result(result)
        run.events.append(
            {
                "timestamp": now,
                "stage": "manual_review",
                "summary": f"Manual review action applied: {action}.",
                "data": {
                    "reviewer": reviewer,
                    "reason": reason,
                    "final_status": result.get("final_status"),
                },
            }
        )
        return {"run_id": run_id, "status": "updated", "run": run.model_dump()}


@app.get("/")
def serve_ui() -> Any:
    index_html = UI_DIST / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return {
        "message": "UI build not found. Start React dev server in ui/ or build ui/dist.",
        "next": [
            "cd ui",
            "npm install",
            "npm run dev",
        ],
    }
