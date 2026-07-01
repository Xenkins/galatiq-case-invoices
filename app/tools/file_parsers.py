from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.schemas.models import InvoiceData, InvoiceItem


def normalize_item_name(item: str) -> str:
    if not item:
        return ""
    cleaned = re.sub(r"\(.*?\)", "", item).strip()
    collapsed = re.sub(r"[\s\-_]+", "", cleaned)
    return collapsed


def _safe_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("$", "").replace(",", "").replace("O", "0")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _safe_int(value: Optional[str]) -> Optional[int]:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = value.strip().replace("2O", "20")
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw if re.match(r"\d{4}-\d{2}-\d{2}", raw) else None


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages).strip()
    except Exception:
        pass

    try:
        import fitz  # type: ignore

        text_parts: List[str] = []
        with fitz.open(path) as doc:
            for page in doc:
                text_parts.append(page.get_text("text"))
        return "\n".join(text_parts).strip()
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        return ""


def _build_invoice(
    source_path: Path,
    invoice_id: str,
    vendor: str,
    date: Optional[str],
    due_date: Optional[str],
    amount: Optional[float],
    items: List[InvoiceItem],
    raw_text: str,
    *,
    due_date_raw: Optional[str] = None,
    payment_terms: Optional[str] = None,
    notes: Optional[str] = None,
) -> InvoiceData:
    return InvoiceData(
        invoice_id=invoice_id or source_path.stem.upper(),
        vendor=(vendor or "").strip(),
        date=date,
        due_date=due_date,
        amount=amount,
        due_date_raw=due_date_raw.strip() if isinstance(due_date_raw, str) else due_date_raw,
        payment_terms=payment_terms.strip() if isinstance(payment_terms, str) else payment_terms,
        notes=notes.strip() if isinstance(notes, str) else notes,
        items=items,
        source_path=str(source_path),
        source_type=source_path.suffix.replace(".", "").lower(),
        raw_text=raw_text,
    )


def _extract_terms_and_notes(text: str) -> Tuple[Optional[str], Optional[str]]:
    payment_terms_match = re.search(
        r"(?im)^\s*(?:Payment\s*Terms|Pymnt\s*Terms|Terms)\s*[:\-]\s*(.+)$",
        text,
    )
    notes_match = re.search(r"(?im)^\s*Notes?\s*[:\-]\s*(.+)$", text)
    payment_terms = payment_terms_match.group(1).strip() if payment_terms_match else None
    notes = notes_match.group(1).strip() if notes_match else None
    return payment_terms, notes


def _parse_txt(path: Path) -> InvoiceData:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return _parse_txt_content(path, text)


def _parse_txt_content(path: Path, text: str) -> InvoiceData:
    lines = [line.rstrip() for line in text.splitlines()]
    whole = "\n".join(lines)

    invoice_id_match = re.search(
        r"(?:Invoice\s*(?:Number)?|Inv\s*#|INV\s*NO|INVOICE\s*#)\s*[:\-]?\s*([A-Z]*[- ]?\d+)",
        whole,
        re.IGNORECASE,
    )
    invoice_id = ""
    if invoice_id_match:
        invoice_id = invoice_id_match.group(1).replace(" ", "-").upper()
        if not invoice_id.startswith("INV-"):
            invoice_id = "INV-" + re.sub(r"[^0-9]", "", invoice_id)

    vendor_match = re.search(
        r"(?:Vendor|Vndr|FROM)\s*[:\-]?\s*(.+)",
        whole,
        re.IGNORECASE,
    )
    vendor = vendor_match.group(1).split("(")[0].strip() if vendor_match else ""

    date_match = re.search(r"(?:Date|Dt)\s*[:\-]?\s*([^\n]+)", whole, re.IGNORECASE)
    due_match = re.search(r"(?:Due\s*Date|Due\s*Dt|DUE)\s*[:\-]?\s*([^\n]+)", whole, re.IGNORECASE)
    due_raw = due_match.group(1).strip() if due_match else None
    amount_matches = re.findall(
        r"(?im)^\s*(?!SUBTOTAL)(?:TOTAL(?:\s*AMOUNT)?|AMT)\s*[:\-]?\s*\$?([0-9,.\-O]+)\s*$",
        whole,
    )
    amount_value = amount_matches[-1] if amount_matches else None
    if amount_value is None:
        amount_match = re.search(
            r"(?:Total\s*Amount|TOTAL|Amt)\s*[:\-]?\s*\$?([0-9,.\-O]+)",
            whole,
            re.IGNORECASE,
        )
        amount_value = amount_match.group(1) if amount_match else None

    items: List[InvoiceItem] = []
    for line in lines:
        parsed = _parse_text_item_line(line)
        if parsed:
            items.append(parsed)

    # If we failed to parse row-wise lines, attempt grouped item blocks.
    if not items:
        item_blocks = re.findall(
            r"-\s*([A-Za-z0-9 \-_]+)\s*x(\d+)\s*\$([0-9,.]+)",
            whole,
            re.IGNORECASE,
        )
        for item_name, qty, unit in item_blocks:
            quantity = _safe_int(qty) or 0
            unit_price = _safe_float(unit)
            line_total = (unit_price or 0.0) * quantity if unit_price is not None else None
            items.append(
                InvoiceItem(
                    item=normalize_item_name(item_name),
                    raw_item=item_name.strip(),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                )
            )

    payment_terms, notes = _extract_terms_and_notes(whole)
    return _build_invoice(
        source_path=path,
        invoice_id=invoice_id,
        vendor=vendor,
        date=_parse_date(date_match.group(1).strip()) if date_match else None,
        due_date=_parse_date(due_raw) if due_raw else None,
        amount=_safe_float(amount_value),
        items=items,
        raw_text=text,
        due_date_raw=due_raw,
        payment_terms=payment_terms,
        notes=notes,
    )


def _parse_text_item_line(line: str) -> Optional[InvoiceItem]:
    cleaned = line.strip()
    if not cleaned or "subtotal" in cleaned.lower() or "total" in cleaned.lower():
        return None

    patterns: List[Tuple[str, Tuple[int, int, int]]] = [
        (r"^([A-Za-z0-9 \-_]+)\s+qty[: ]+\s*(-?\d+).*?\$([0-9,.\-O]+)", (1, 2, 3)),
        (r"^([A-Za-z0-9 \-_]+)\s+qty\s*(-?\d+).*?\$([0-9,.\-O]+)", (1, 2, 3)),
        (r"^([A-Za-z0-9 \-_]+)\s+x(\d+)\s+\$([0-9,.\-O]+)", (1, 2, 3)),
        (r"^([A-Za-z0-9 \-_()]+)\s+(-?\d+)\s+\$([0-9,.\-O]+)\s+\$([0-9,.\-O]+)", (1, 2, 3)),
    ]
    for pattern, groups in patterns:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if not m:
            continue
        item_name = m.group(groups[0]).strip()
        quantity = _safe_int(m.group(groups[1])) or 0
        unit_price = _safe_float(m.group(groups[2]))
        line_total = None
        if len(m.groups()) >= 4:
            line_total = _safe_float(m.group(4))
        if line_total is None and unit_price is not None:
            line_total = quantity * unit_price
        return InvoiceItem(
            item=normalize_item_name(item_name),
            raw_item=item_name,
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
        )
    return None


def _parse_json(path: Path) -> InvoiceData:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    line_items = data.get("line_items", [])
    items: List[InvoiceItem] = []
    for li in line_items:
        raw_name = str(li.get("item") or li.get("name") or "").strip()
        quantity = int(li.get("quantity", 0))
        unit_price = _safe_float(str(li.get("unit_price")))
        amount = _safe_float(str(li.get("amount"))) or (
            (unit_price or 0.0) * quantity if unit_price is not None else None
        )
        items.append(
            InvoiceItem(
                item=normalize_item_name(raw_name),
                raw_item=raw_name,
                quantity=quantity,
                unit_price=unit_price,
                line_total=amount,
            )
        )
    vendor = data.get("vendor", {})
    if isinstance(vendor, dict):
        vendor_name = vendor.get("name", "")
    else:
        vendor_name = str(vendor)

    return _build_invoice(
        source_path=path,
        invoice_id=str(data.get("invoice_number") or data.get("invoice_id") or "").strip(),
        vendor=vendor_name,
        date=_parse_date(str(data.get("date")) if data.get("date") is not None else None),
        due_date=_parse_date(str(data.get("due_date")) if data.get("due_date") is not None else None),
        amount=_safe_float(str(data.get("total"))) or _safe_float(str(data.get("amount"))),
        items=items,
        raw_text=raw,
        due_date_raw=str(data.get("due_date")).strip() if data.get("due_date") is not None else None,
        payment_terms=str(data.get("payment_terms")).strip()
        if data.get("payment_terms") is not None
        else None,
        notes=str(data.get("notes")).strip() if data.get("notes") is not None else None,
    )


def _parse_csv(path: Path) -> InvoiceData:
    raw = path.read_text(encoding="utf-8")
    rows = list(csv.DictReader(raw.splitlines()))
    items: List[InvoiceItem] = []

    # Format A: key/value rows
    if rows and set(rows[0].keys()) == {"field", "value"}:
        key_values: Dict[str, List[str]] = {}
        for row in rows:
            key = (row.get("field") or "").strip().lower()
            value = (row.get("value") or "").strip()
            key_values.setdefault(key, []).append(value)

        item_values = key_values.get("item", [])
        qty_values = key_values.get("quantity", [])
        unit_values = key_values.get("unit_price", [])
        for idx, raw_item in enumerate(item_values):
            qty = _safe_int(qty_values[idx] if idx < len(qty_values) else None) or 0
            unit = _safe_float(unit_values[idx] if idx < len(unit_values) else None)
            items.append(
                InvoiceItem(
                    item=normalize_item_name(raw_item),
                    raw_item=raw_item,
                    quantity=qty,
                    unit_price=unit,
                    line_total=(unit or 0.0) * qty if unit is not None else None,
                )
            )
        return _build_invoice(
            source_path=path,
            invoice_id=(key_values.get("invoice_number") or [""])[0],
            vendor=(key_values.get("vendor") or [""])[0],
            date=_parse_date((key_values.get("date") or [None])[0]),
            due_date=_parse_date((key_values.get("due_date") or [None])[0]),
            amount=_safe_float((key_values.get("total") or [None])[0]),
            items=items,
            raw_text=raw,
            due_date_raw=(key_values.get("due_date") or [None])[0],
            payment_terms=(key_values.get("payment_terms") or [None])[0],
        )

    # Format B: tabular rows with repeated invoice fields.
    invoice_id = ""
    vendor = ""
    date = None
    due_date = None
    due_date_raw = None
    amount = None
    payment_terms = None
    for row in rows:
        inv = (row.get("Invoice Number") or "").strip()
        if inv and not invoice_id:
            invoice_id = inv
        vend = (row.get("Vendor") or "").strip()
        if vend and not vendor:
            vendor = vend
        row_date = (row.get("Date") or "").strip()
        if row_date and not date:
            date = _parse_date(row_date)
        row_due = (row.get("Due Date") or "").strip()
        if row_due and not due_date:
            due_date_raw = row_due
            due_date = _parse_date(row_due)
        if payment_terms is None:
            maybe_terms = (row.get("Payment Terms") or row.get("Terms") or "").strip()
            if maybe_terms:
                payment_terms = maybe_terms
        raw_item = (row.get("Item") or "").strip()
        qty = _safe_int(row.get("Qty"))
        unit = _safe_float(row.get("Unit Price"))
        line_total = _safe_float(row.get("Line Total"))
        if raw_item and qty is not None and unit is not None:
            items.append(
                InvoiceItem(
                    item=normalize_item_name(raw_item),
                    raw_item=raw_item,
                    quantity=qty,
                    unit_price=unit,
                    line_total=line_total if line_total is not None else qty * unit,
                )
            )
        label = (row.get("Unit Price") or "").strip().lower()
        if label == "total:":
            amount = _safe_float(row.get("Line Total"))

    if amount is None:
        amount = sum((item.line_total or 0.0) for item in items) if items else None

    return _build_invoice(
        source_path=path,
        invoice_id=invoice_id,
        vendor=vendor,
        date=date,
        due_date=due_date,
        amount=amount,
        items=items,
        raw_text=raw,
        due_date_raw=due_date_raw,
        payment_terms=payment_terms,
    )


def _parse_xml(path: Path) -> InvoiceData:
    raw = path.read_text(encoding="utf-8")
    root = ET.fromstring(raw)
    header = root.find("header")
    totals = root.find("totals")
    items_root = root.find("line_items")
    items: List[InvoiceItem] = []
    if items_root is not None:
        for node in items_root.findall("item"):
            raw_name = (node.findtext("name") or "").strip()
            qty = _safe_int(node.findtext("quantity")) or 0
            unit = _safe_float(node.findtext("unit_price"))
            items.append(
                InvoiceItem(
                    item=normalize_item_name(raw_name),
                    raw_item=raw_name,
                    quantity=qty,
                    unit_price=unit,
                    line_total=(unit or 0.0) * qty if unit is not None else None,
                )
            )

    return _build_invoice(
        source_path=path,
        invoice_id=(header.findtext("invoice_number") if header is not None else "") or "",
        vendor=(header.findtext("vendor") if header is not None else "") or "",
        date=_parse_date(header.findtext("date") if header is not None else None),
        due_date=_parse_date(header.findtext("due_date") if header is not None else None),
        amount=_safe_float(totals.findtext("total") if totals is not None else None),
        items=items,
        raw_text=raw,
        due_date_raw=(header.findtext("due_date") if header is not None else None),
        payment_terms=(root.findtext("payment_terms") or None),
    )


def _parse_pdf(path: Path) -> InvoiceData:
    text = _extract_pdf_text(path)
    if not text:
        sibling_txt = path.with_suffix(".txt")
        sibling_json = path.with_suffix(".json")
        if sibling_txt.exists():
            parsed = _parse_txt(sibling_txt)
            parsed.source_type = "pdf"
            parsed.source_path = str(path)
            return parsed
        if sibling_json.exists():
            parsed = _parse_json(sibling_json)
            parsed.source_type = "pdf"
            parsed.source_path = str(path)
            return parsed
        return _build_invoice(
            source_path=path,
            invoice_id=path.stem.upper(),
            vendor="",
            date=None,
            due_date=None,
            amount=None,
            items=[],
            raw_text="",
        )
    temp = path.with_suffix(".txt")
    parsed = _parse_txt(temp) if temp.exists() else _parse_txt_from_text(path, text)
    parsed.source_type = "pdf"
    parsed.source_path = str(path)
    parsed.raw_text = text
    return parsed


def _parse_txt_from_text(source_path: Path, text: str) -> InvoiceData:
    return _parse_txt_content(source_path, text)


def parse_invoice_file(path: str) -> InvoiceData:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".txt":
        return _parse_txt(p)
    if suffix == ".json":
        return _parse_json(p)
    if suffix == ".csv":
        return _parse_csv(p)
    if suffix == ".xml":
        return _parse_xml(p)
    if suffix == ".pdf":
        return _parse_pdf(p)
    raise ValueError(f"Unsupported invoice format: {suffix}")
