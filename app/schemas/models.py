from __future__ import annotations

from dataclasses import asdict, dataclass, field as dataclass_field
from datetime import datetime
from typing import Any, Dict, List, Optional


SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

DECISION_APPROVE = "APPROVE"
DECISION_HUMAN_REVIEW = "HUMAN_REVIEW"
DECISION_REJECT = "REJECT"


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    stage: str
    field: Optional[str] = None
    details: Dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class MatchCandidate:
    candidate: str
    confidence: float


@dataclass
class InvoiceItem:
    item: str
    quantity: int
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    raw_item: Optional[str] = None


@dataclass
class InvoiceData:
    invoice_id: str
    vendor: str
    date: Optional[str]
    due_date: Optional[str]
    amount: Optional[float]
    items: List[InvoiceItem] = dataclass_field(default_factory=list)
    source_path: str = ""
    source_type: str = ""
    raw_text: str = ""
    metadata: Dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class ValidationResult:
    validation_pass: bool
    requires_human_review: bool
    item_checks: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    fuzzy_candidates: Dict[str, List[MatchCandidate]] = dataclass_field(default_factory=dict)
    totals_check: Dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class ApprovalResult:
    decision: str
    rationale: str
    requires_human_review: bool
    policy_flags: List[str] = dataclass_field(default_factory=list)


@dataclass
class PaymentResult:
    status: str
    message: str
    transaction_id: Optional[str] = None


@dataclass
class ReflectionResult:
    status: str  # pass | retry | fail
    feedback: str
    confidence: float
    checks: List[str] = dataclass_field(default_factory=list)


@dataclass
class PipelineState:
    invoice_path: str
    invoice_data: Optional[InvoiceData] = None
    validation_result: Optional[ValidationResult] = None
    approval_result: Optional[ApprovalResult] = None
    payment_result: Optional[PaymentResult] = None
    issues: List[Issue] = dataclass_field(default_factory=list)
    audit_log: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    retry_counts: Dict[str, int] = dataclass_field(
        default_factory=lambda: {"ingest": 0, "validate": 0, "approve": 0}
    )
    needs_human_review: bool = False
    final_status: str = "IN_PROGRESS"
    reflection_feedback: Dict[str, str] = dataclass_field(default_factory=dict)

    def add_issue(self, issue: Issue) -> None:
        self.issues.append(issue)
        if issue.severity in {SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL}:
            self.needs_human_review = True

    def log_stage(self, stage: str, summary: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.audit_log.append(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "stage": stage,
                "summary": summary,
                "data": data or {},
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
