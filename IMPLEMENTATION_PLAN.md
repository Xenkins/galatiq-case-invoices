# Implementation Plan

This plan is organized to deliver a working MVP quickly, then layer in reliability and polish.

## Principles

- Deterministic checks decide safety-critical outcomes.
- LLMs assist with extraction, critique, and rationale.
- Any uncertainty routes to human review.
- One retry max per reflection stage to control latency and complexity.

## Phase 0: Project Setup (Day 0)

Objective: create a stable local foundation.

Tasks:
- Initialize Python environment and dependencies.
- Create baseline project structure (`agents`, `tools`, `schemas`, `graph`, `tests`).
- Add `.env.example` for API configuration.
- Add basic logging configuration and run script.

Deliverable:
- `python main.py --help` works.
- No-op graph skeleton executes with a sample invoice path.

## Phase 1: Core Data Contracts (Day 1)

Objective: define strict schemas for all stage boundaries.

Tasks:
- Create normalized invoice schema (`InvoiceData`, `InvoiceItem`).
- Create stage output schemas:
  - `ValidationResult`
  - `ApprovalResult`
  - `PaymentResult`
  - `Issue`
- Add schema validation helpers and serialization utilities.

Deliverable:
- All nodes consume/produce typed structures.
- Schema unit tests for valid/invalid payloads.

## Phase 2: Ingestion Agent + Reflection (Day 1-2)

Objective: parse all required file types into one schema.

Tasks:
- Implement parser router by extension (`pdf`, `txt`, `csv`, `json`, `xml`).
- Build per-format extraction functions.
- Add normalization layer for field naming and type coercion.
- Implement ingestion reflection node:
  - required fields check
  - date parsing check
  - amount and quantity sanity checks
  - totals consistency check
- Add one bounded retry path with targeted feedback.

Deliverable:
- Ingestion works on provided sample files.
- Reflection catches malformed extraction and retries once.

## Phase 3: Validation Agent + Reflection (Day 2)

Objective: reconcile extracted data against local SQLite inventory.

Tasks:
- Implement DB setup and connection helpers.
- Seed inventory table per case requirements.
- Implement deterministic validation checks:
  - unknown item
  - stock mismatch
  - non-positive quantity
  - suspicious zero-stock entries
- Add optional fuzzy candidate generation for unresolved item names.
- Implement validation reflection node:
  - ensure every item has verdict
  - ensure severities are consistent
  - verify `validation_pass` logic

Deliverable:
- Validation report with issue codes and severity.
- Ambiguous matches marked for manual review.

## Phase 4: Approval Agent + Reflection (Day 3)

Objective: policy-based routing to approve/review/reject.

Tasks:
- Implement policy engine with explicit rule table:
  - high-value invoice threshold (e.g., > $10k)
  - any critical issue => reject
  - ambiguous match => human review
- Implement approval rationale generator.
- Implement approval reflection node:
  - policy-decision consistency
  - rationale completeness and traceability

Deliverable:
- Approval decisions are deterministic and explainable.
- Reflection allows one correction pass when rationale or routing is inconsistent.

## Phase 5: Payment + Final Supervisor (Day 3)

Objective: execute payments safely and finalize audit trace.

Tasks:
- Implement payment node with guardrails (approved only).
- Integrate mock payment function.
- Implement final supervisor node:
  - cross-stage consistency checks
  - no payment for rejected/review invoices
  - approved invoices must include payment result
- Emit final output envelope:
  - `final_status`
  - `issues`
  - `audit_log`
  - `next_action`

Deliverable:
- End-to-end run for success and rejection cases.

## Phase 6: LangGraph Orchestration (Day 4)

Objective: wire nodes and conditional routing.

Tasks:
- Build graph:
  - `ingest -> ingest_reflect`
  - `validate -> validate_reflect`
  - `approve -> approve_reflect`
  - `payment -> supervisor`
- Add conditional edges for retry, reject, human review, and payment path.
- Add runtime tracing and stage-level metrics.

Deliverable:
- Full graph compiles and runs through all branches.

## Phase 7: Test Matrix and Hardening (Day 4-5)

Objective: prove reliability and business correctness.

Tasks:
- Unit tests for tools and rule functions.
- Integration tests for each stage.
- End-to-end tests across representative invoices:
  - clean pass
  - stock mismatch
  - unknown item
  - invalid quantity
  - high-value scrutiny
  - ambiguous fuzzy match requiring review
- Add regression snapshots for final output format.

Deliverable:
- Repeatable test suite with clear pass/fail signals.

## Phase 8: UX/Presentation Layer (Optional, Day 5+)

Objective: improve reviewability without jeopardizing MVP delivery.

Tasks:
- Add minimal web UI or CLI report view.
- Display per-stage status, issues, and decisions.
- Add manual review action capture for ambiguous cases.

Deliverable:
- Lightweight operator experience for demo.

## Suggested Project Structure

```text
.
├── main.py
├── README.md
├── IMPLEMENTATION_PLAN.md
├── data/
│   ├── invoices/
│   └── generate_pdfs.py
├── app/
│   ├── graph/
│   │   ├── state.py
│   │   ├── builder.py
│   │   └── routes.py
│   ├── agents/
│   │   ├── ingestion.py
│   │   ├── validation.py
│   │   ├── approval.py
│   │   ├── payment.py
│   │   └── supervisor.py
│   ├── reflection/
│   │   ├── ingestion_reflect.py
│   │   ├── validation_reflect.py
│   │   └── approval_reflect.py
│   ├── tools/
│   │   ├── file_parsers.py
│   │   ├── db.py
│   │   ├── fuzzy_match.py
│   │   └── payment.py
│   ├── policies/
│   │   └── approval_rules.py
│   └── schemas/
│       └── models.py
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

## Definition of Done

The implementation is complete when:

- CLI processes any supported invoice format.
- Graph executes end-to-end with reflection loops.
- Validation reliably catches mismatches against SQLite.
- Ambiguity routes to manual review (never silent auto-remap).
- Payment occurs only when approval is explicit.
- Structured logs and audit trail are emitted.
- Test suite covers core success and failure paths.
