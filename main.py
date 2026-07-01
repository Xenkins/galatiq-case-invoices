from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict

from app.graph.builder import pretty_result, run_invoice_pipeline

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - fallback when dependency not installed
    load_dotenv = None

POLICY_FLAG_EXPLANATIONS = {
    "critical_issue_present": "A critical integrity/risk issue was found, so auto-processing stops.",
    "error_issue_present": "An error-level validation issue requires manual reviewer confirmation.",
    "validation_not_passed": "Validation did not fully pass, so auto-approval is blocked.",
    "uncertainty_requires_review": "Signals are ambiguous enough that a human must verify before payment.",
    "vp_threshold_exceeded": "Invoice amount exceeds escalation threshold and needs higher-level review.",
    "payment_pressure_language_detected": "Invoice language suggests urgency pressure (potential social-engineering risk).",
    "nonstandard_payment_instruction": "Payment instructions look non-standard (e.g., wire/new account/crypto).",
    "payment_terms_note_conflict": "Payment notes conflict with stated terms and need verification.",
    "manual_review_override": "Final decision was changed by a manual reviewer override.",
}


def parse_args() -> argparse.Namespace:
    default_db_path = os.getenv("INVENTORY_DB_PATH", "inventory.db")
    default_vp_threshold = float(os.getenv("VP_APPROVAL_THRESHOLD", "10000"))
    default_grok_model = os.getenv("GROK_MODEL", "grok-3")

    parser = argparse.ArgumentParser(description="Multi-agent invoice processing pipeline")
    parser.add_argument("--invoice_path", required=True, help="Path to invoice file.")
    parser.add_argument(
        "--db_path",
        default=default_db_path,
        help="Path to SQLite inventory database.",
    )
    parser.add_argument(
        "--vp_threshold",
        type=float,
        default=default_vp_threshold,
        help="Invoices above this amount require human review.",
    )
    parser.add_argument(
        "--grok_api_key",
        default=None,
        help="Grok API key override. If omitted, uses GROK_API_KEY env var.",
    )
    parser.add_argument(
        "--grok_model",
        default=default_grok_model,
        help="Grok model slug to use for LLM-assisted stages.",
    )
    return parser.parse_args()


def _human_stage_name(stage: str) -> str:
    mapping = {
        "ingestion": "Ingestion",
        "ingest_reflect": "Ingestion Reflection",
        "validation": "Validation",
        "validate_reflect": "Validation Reflection",
        "approval": "Approval",
        "approve_reflect": "Approval Reflection",
        "payment": "Payment",
        "supervisor": "Supervisor",
        "run_failed": "Run Failure",
    }
    return mapping.get(stage, stage.replace("_", " ").title())


def _format_event_line(event: Dict[str, Any]) -> str:
    stage = _human_stage_name(str(event.get("stage", "unknown")))
    summary = str(event.get("summary", "")).strip()
    data = event.get("data") or {}
    details: list[str] = []

    for key in ("status", "decision", "final_status", "invoice_id", "validation_pass"):
        if key in data:
            details.append(f"{key}={data[key]}")
    if "issue_count" in data:
        details.append(f"issues={data['issue_count']}")
    if "item_count" in data:
        details.append(f"items={data['item_count']}")
    if "feedback" in data and data["feedback"]:
        details.append(f"feedback={data['feedback']}")

    details_str = f" ({', '.join(details)})" if details else ""
    return f"[{stage}] {summary}{details_str}"


def _print_final_summary(summary: Dict[str, Any]) -> None:
    print("\n=== Final Result ===")
    print(
        "Invoice {invoice_id} from {vendor}: final_status={final_status}, "
        "decision={decision}, payment_status={payment_status}, issues={issue_count}".format(
            invoice_id=summary.get("invoice_id", "unknown"),
            vendor=summary.get("vendor", "unknown"),
            final_status=summary.get("final_status", "unknown"),
            decision=summary.get("decision", "unknown"),
            payment_status=summary.get("payment_status", "unknown"),
            issue_count=summary.get("issue_count", 0),
        )
    )


def _print_flag_details(result: Dict[str, Any]) -> None:
    approval = result.get("approval_result") or {}
    issues = result.get("issues") or []
    policy_flags = list(approval.get("policy_flags") or [])

    print("\n=== Flags and Reasons ===")
    if not policy_flags and not issues:
        print("No flags were raised. Invoice passed deterministic checks and policy gates.")
        return

    if policy_flags:
        print("Policy flags:")
        for flag in policy_flags:
            explanation = POLICY_FLAG_EXPLANATIONS.get(flag, "Policy escalation flag from approval logic.")
            print(f"- {flag}: {explanation}")

    if issues:
        print("Validation / processing issues:")
        for issue in issues:
            severity = str(issue.get("severity") or "unknown").upper()
            stage = str(issue.get("stage") or "unknown")
            code = str(issue.get("code") or "UNKNOWN_ISSUE")
            message = str(issue.get("message") or "").strip()
            print(f"- [{severity}] {code} (stage={stage}): {message}")
            details = issue.get("details") or {}
            candidates = details.get("candidates")
            if isinstance(candidates, list) and candidates:
                pretty = ", ".join(str(c) for c in candidates)
                print(f"  Suggested matches: {pretty}")


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()

    args = parse_args()
    invoice_path = Path(args.invoice_path)
    if not invoice_path.exists():
        raise SystemExit(f"Invoice file not found: {invoice_path}")

    print(f"Starting invoice workflow for: {invoice_path}")
    try:
        result = run_invoice_pipeline(
            invoice_path=str(invoice_path),
            db_path=args.db_path,
            vp_threshold=args.vp_threshold,
            grok_api_key=args.grok_api_key or os.getenv("GROK_API_KEY"),
            grok_model=args.grok_model,
            on_event=lambda event: print(_format_event_line(event)),
        )
    except Exception as exc:
        raise SystemExit(f"Pipeline failed: {exc}")
    summary = pretty_result(result)
    _print_final_summary(summary)
    _print_flag_details(result)


if __name__ == "__main__":
    main()
