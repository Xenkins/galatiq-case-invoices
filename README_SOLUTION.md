# Galatiq Invoice Automation (Take-Home Solution)

End-to-end, multi-agent invoice processing pipeline for Acme Corp using LangGraph and Grok-style reasoning.

## Problem

Acme Corp processes invoices manually and is losing money due to errors, delays, and inconsistent approvals. The goal is to automate invoice handling safely across four stages:

1. Ingestion
2. Validation
3. Approval
4. Payment

This project focuses on both speed and controls: maximize straight-through processing for clean invoices while routing uncertain or risky cases to a human reviewer.

## Solution Overview

The system is implemented as a LangGraph workflow with one worker agent per stage plus bounded reflection loops.

- `Ingestion Agent` parses multi-format invoices (`pdf`, `txt`, `csv`, `json`, `xml`) into one normalized schema.
- `Validation Agent` reconciles extracted items against local SQLite inventory.
- `Approval Agent` applies deterministic policy rules and risk checks.
- `Payment Agent` executes mock payment only for approved invoices.
- `Reflection Gates` critique each stage output and allow at most one retry.
- `Final Supervisor` performs cross-stage consistency checks and emits audit-ready output.

## Why This Architecture

- Prevents upstream extraction errors from silently propagating.
- Keeps deterministic business controls as decision authority.
- Uses LLM reasoning where it adds value (unstructured parsing, critique, rationale).
- Produces explainable outcomes with machine-readable logs.

## Safety and Decision Policy

The system classifies outcomes into three decisions:

- `AUTO_APPROVE`: all checks pass, high confidence, no policy escalations.
- `HUMAN_REVIEW`: ambiguity or risk (fuzzy candidate match, conflicting fields, low confidence, high-value threshold).
- `REJECT`: critical validation failures (unknown item with no acceptable mapping, negative quantity, policy violation).

### Fuzzy Matching Policy

Fuzzy matching is suggestion-only.

- If no exact DB match exists, the validator may return `candidate_matches` with confidence scores.
- The system does **not** auto-remap ambiguous item/vendor keys.
- Any uncertain match requires manual approval before continuing.

## Agent and Tool Responsibilities

### 1) Ingestion Agent

Input: invoice file path  
Output: normalized `InvoiceData`

Tools:
- file readers by type (`pdf`, `txt`, `csv`, `json`, `xml`)
- schema normalizer
- optional LLM extraction for messy text/OCR artifacts

Reflection checks:
- required fields present
- valid data types and parsable dates
- line-item and total consistency

### 2) Validation Agent

Input: `InvoiceData`  
Output: structured validation report

Tools:
- SQLite inventory queries
- deterministic business rule checks
- optional fuzzy candidate generation

Checks:
- unknown items
- quantity <= 0
- quantity exceeds stock
- suspicious or zero-stock items

### 3) Approval Agent

Input: invoice + validation report  
Output: decision (`approve`, `review`, `reject`) + rationale

Tools:
- policy engine (ex: invoices above $10k require elevated scrutiny)
- risk scoring helper

Reflection checks:
- policy compliance
- consistency with validation severity
- complete, auditable explanation

### 4) Payment Agent

Input: approved decision  
Output: payment result

Tools:
- mock payment function
- transaction logger

Guardrails:
- never execute if decision is `review` or `reject`
- always log attempted and completed payment states

### 5) Final Supervisor

Cross-stage audit checks:
- no payment occurred for rejected/reviewed invoices
- approved invoices include payment result
- rationale and issues are present and coherent

## Data Contract (Normalized)

Each invoice is normalized to a single schema before validation:

```json
{
  "invoice_id": "INV-1012",
  "vendor": "QuickShip Distributers",
  "due_date": "2026-02-25",
  "amount": 9975.0,
  "items": [
    {"item": "WidgetA", "quantity": 12, "unit_price": 250.0, "line_total": 3000.0}
  ],
  "source_path": "data/invoices/invoice_1012.pdf",
  "source_type": "pdf"
}
```

## Runtime and Stack

- Python 3.11+
- LangGraph (or LangChain graph primitives)
- SQLite for local validation store
- `pdfplumber` and/or `PyMuPDF` for PDF extraction
- Optional: Grok via xAI API (fallback model allowed)
- Local-only execution; no external business dependencies required

## Input Dataset

Sample invoices are in `data/invoices/` across multiple formats. They include both clean and problematic cases for validation and decision testing.

Optional utility:
- `data/generate_pdfs.py` generates representative PDF invoices for additional parser testing.

## CLI Contract

Environment setup (recommended):

```bash
cp .env.example .env
# then edit .env and set GROK_API_KEY
```

Note: this implementation requires Grok + LangGraph on every run. If Grok is unavailable, the pipeline exits with an error.

Example entrypoint:

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt
```

Grok-enabled run:

```bash
export GROK_API_KEY="your_key"
python main.py --invoice_path=data/invoices/invoice_1001.txt --grok_model=grok-3
```

Expected output:
- structured stage logs
- final decision (`approved_paid`, `human_review`, `rejected`)
- machine-readable issue list
- audit trace for each stage and reflection pass

## React UI (Live Orchestration View)

The project includes a React UI that visualizes stage-by-stage orchestration events from the LangGraph run.

Optional UI mode (for orchestration visualization):

Run backend API:

```bash
uvicorn app.api_server:app --reload --port 8000
```

In another terminal, run React:

```bash
cd ui
npm install
npm run dev
```

Open `http://127.0.0.1:5173`, choose an invoice, and run. The timeline updates as each stage logs events.

## Implementation Roadmap

See `IMPLEMENTATION_PLAN.md` for phased delivery, milestones, and test strategy.

## Evaluation Alignment

This design is optimized for the case rubric:

- Functionality: true end-to-end flow
- Code quality: typed contracts, deterministic checks, error handling
- Agentic sophistication: multi-agent orchestration + reflection
- Shipping mindset: bounded retries, clear MVP scope
- Presentation: business-first explanation with auditability
