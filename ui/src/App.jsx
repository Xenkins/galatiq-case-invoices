import { useEffect, useMemo, useState } from "react";

const POLL_MS = 900;
const PROCESS_STEPS = [
  { id: "ingestion", label: "Ingestion", event: "ingestion" },
  { id: "validation", label: "Validation", event: "validation" },
  { id: "approval", label: "Approval", event: "approval" },
  { id: "payment", label: "Payment", event: "payment" },
];

function StatusPill({ status }) {
  const cls =
    status === "completed"
      ? "pill pill-ok"
      : status === "failed"
      ? "pill pill-err"
      : "pill pill-run";
  return <span className={cls}>{status ?? "idle"}</span>;
}

function summarizeStep(stepId, { state, event, invoiceData, validation, approval, payment, issues }) {
  if (event) {
    const data = event.data || {};
    if (stepId === "ingestion") {
      const invoiceId = data.invoice_id || invoiceData?.invoice_id || "unknown";
      const vendor = data.vendor || invoiceData?.vendor || "unknown vendor";
      const amountRaw = data.amount ?? invoiceData?.amount;
      const amount = Number.isFinite(Number(amountRaw))
        ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
            Number(amountRaw),
          )
        : "unknown amount";
      return `Parsed invoice ${invoiceId} from ${vendor} for ${amount}.`;
    }
    if (stepId === "validation") {
      const validationIssues = issues.filter((issue) => issue.stage === "validation").length;
      const issueCount = Number(data.issue_count ?? validationIssues);
      const requiresReview = Boolean(
        data.requires_human_review ?? (validationIssues > 0 || validation?.requires_human_review),
      );
      if (issueCount > 0 || requiresReview) {
        return `Validation flagged ${issueCount} issue${
          issueCount === 1 ? "" : "s"
        } that require review.`;
      }
      return "Validation passed with no flags.";
    }
    if (stepId === "approval") {
      const decision = data.decision || approval?.decision;
      if (decision === "APPROVE") {
        return "Approval decision is APPROVE. Invoice can proceed to payment.";
      }
      if (decision === "HUMAN_REVIEW") {
        return "Approval decision escalated to HUMAN_REVIEW.";
      }
      if (decision === "REJECT") {
        return "Approval decision is REJECT due to policy risk.";
      }
      return event.summary || "Approval policy applied.";
    }
    if (stepId === "payment") {
      if (payment?.status === "success") return "Payment executed successfully.";
      if (payment?.status === "skipped") return "Payment skipped because invoice was not approved.";
      if (event.summary) return event.summary;
      return "Payment step completed.";
    }
  }

  if (state === "active") {
    if (stepId === "ingestion") return "Parsing and normalizing invoice data...";
    if (stepId === "validation") return "Checking inventory, totals, and policy constraints...";
    if (stepId === "approval") return "Evaluating approval policy and decision rationale...";
    if (stepId === "payment") return "Executing payment or skip decision...";
  }

  if (stepId === "ingestion") {
    if (!invoiceData) return "Waiting to parse and normalize invoice data.";
    const amount = Number.isFinite(Number(invoiceData.amount))
      ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
          Number(invoiceData.amount),
        )
      : "unknown amount";
    return `Parsed invoice ${invoiceData.invoice_id || "unknown"} from ${
      invoiceData.vendor || "unknown vendor"
    } for ${amount}.`;
  }

  if (stepId === "validation") {
    if (!validation) return "Waiting to validate inventory and invoice consistency.";
    const validationIssues = issues.filter((issue) => issue.stage === "validation");
    if (!validationIssues.length && validation.validation_pass) {
      return "Validation passed with no flags.";
    }
    return `Validation flagged ${validationIssues.length} issue${
      validationIssues.length === 1 ? "" : "s"
    } that require review.`;
  }

  if (stepId === "approval") {
    if (!approval) return "Waiting for policy decision.";
    if (approval.decision === "APPROVE") {
      return "Approval decision is APPROVE. Invoice can proceed to payment.";
    }
    if (approval.decision === "HUMAN_REVIEW") {
      return "Approval decision escalated to HUMAN_REVIEW.";
    }
    if (approval.decision === "REJECT") {
      return "Approval decision is REJECT due to policy risk.";
    }
    return `Approval decision: ${approval.decision}.`;
  }

  if (stepId === "payment") {
    if (!payment) return "Waiting for payment execution or skip decision.";
    if (payment.status === "success") {
      return "Payment executed successfully.";
    }
    if (payment.status === "skipped") {
      return "Payment skipped because invoice was not approved.";
    }
    return `Payment status: ${payment.status}.`;
  }

  return "Processing step update.";
}

function App() {
  const [invoices, setInvoices] = useState([]);
  const [invoicePath, setInvoicePath] = useState("data/invoices/invoice_1001.txt");
  const [runId, setRunId] = useState(null);
  const [runState, setRunState] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [activeTab, setActiveTab] = useState("timeline");
  const [reviewer, setReviewer] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [isSubmittingReview, setIsSubmittingReview] = useState(false);
  const [reviewMessage, setReviewMessage] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [error, setError] = useState("");

  const loadInvoices = async () => {
    const res = await fetch("/api/invoices");
    const data = await res.json();
    setInvoices(data.invoices ?? []);
    if ((data.invoices ?? []).length > 0) {
      setInvoicePath((prev) => prev || data.invoices[0]);
    }
  };

  useEffect(() => {
    loadInvoices().catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (!runId) return undefined;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/runs/${runId}`);
        const data = await res.json();
        if (!active) return;
        setRunState(data);
        if (data.status === "completed" || data.status === "failed") {
          clearInterval(timer);
        }
      } catch (err) {
        if (active) setError(String(err));
      }
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [runId]);

  const timeline = useMemo(() => runState?.events ?? [], [runState]);
  const result = runState?.result ?? null;
  const issues = result?.issues ?? [];
  const invoiceData = result?.invoice_data ?? null;
  const validation = result?.validation_result ?? null;
  const approval = result?.approval_result ?? null;
  const payment = result?.payment_result ?? null;
  const sourcePath = invoiceData?.source_path || invoicePath;
  const sourceExt = String(sourcePath || "")
    .toLowerCase()
    .split(".")
    .pop();
  const sourceUrl = sourcePath ? `/api/source?path=${encodeURIComponent(sourcePath)}` : "";

  const issueGroups = useMemo(() => {
    const grouped = new Map();
    for (const issue of issues) {
      const key = issue.stage || "unknown";
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(issue);
    }
    return Array.from(grouped.entries());
  }, [issues]);

  const inventoryRows = useMemo(() => {
    const checks = validation?.item_checks ?? [];
    return checks.map((check, idx) => {
      const requested = Number(check.quantity ?? 0);
      const stockRaw = check.stock;
      const stock = stockRaw === undefined || stockRaw === null ? null : Number(stockRaw);
      const delta = stock === null || Number.isNaN(stock) ? null : stock - requested;
      const statusRaw = String(check.status || "").toLowerCase();
      let label = "Needs Review";
      let tone = "warn";
      if (statusRaw === "ok") {
        label = "In Stock";
        tone = "ok";
      } else if (statusRaw === "stock_mismatch") {
        label = "Insufficient Stock";
        tone = "err";
      } else if (statusRaw === "out_of_stock") {
        label = "Out of Stock";
        tone = "err";
      } else if (statusRaw === "unknown_item") {
        label = "Unknown Item";
        tone = "err";
      } else if (statusRaw === "ambiguous_fuzzy_match") {
        label = "Ambiguous Match";
        tone = "warn";
      } else if (statusRaw === "invalid_quantity") {
        label = "Invalid Quantity";
        tone = "err";
      }
      return {
        id: `${check.item || "item"}-${idx}`,
        item: check.matched_inventory_item || check.item || "N/A",
        requested,
        stock,
        delta,
        label,
        tone,
      };
    });
  }, [validation]);

  const highestSeverity = useMemo(() => {
    const rank = { critical: 4, error: 3, warning: 2, info: 1 };
    let top = "none";
    let topScore = 0;
    for (const issue of issues) {
      const sev = String(issue.severity || "").toLowerCase();
      const score = rank[sev] || 0;
      if (score > topScore) {
        top = sev;
        topScore = score;
      }
    }
    return top;
  }, [issues]);

  const approvalNarrative = useMemo(() => {
    if (!approval) return "Approval has not run yet.";
    if (approval.rationale) return approval.rationale;
    if (approval.decision === "APPROVE") {
      return "All policy checks passed and the invoice is approved for payment.";
    }
    if (approval.decision === "HUMAN_REVIEW") {
      return "Invoice was escalated to human review due to policy or validation risk signals.";
    }
    if (approval.decision === "REJECT") {
      return "Invoice was rejected due to critical risk or policy violations.";
    }
    return "Approval policy executed.";
  }, [approval]);

  const steps = useMemo(() => {
    const hasEvent = (name) =>
      timeline.some((event) => (event.stage || "").toLowerCase() === name.toLowerCase());
    const latestEventFor = (name) =>
      [...timeline]
        .reverse()
        .find((event) => (event.stage || "").toLowerCase() === name.toLowerCase()) || null;
    const statuses = PROCESS_STEPS.map((step) => {
      const done = hasEvent(step.event);
      return {
        ...step,
        done,
        flagged: issues.some((issue) => issue.stage === step.id),
        event: latestEventFor(step.event),
      };
    });
    const firstPendingIndex = statuses.findIndex((step) => !step.done);
    return statuses.map((step, idx) => {
      let state = "pending";
      if (step.done) state = step.flagged ? "flagged" : "done";
      if (!step.done && runState?.status === "running" && idx === firstPendingIndex) {
        state = "active";
      }
      if (
        step.id === "payment" &&
        approval?.decision &&
        approval.decision !== "APPROVE" &&
        payment?.status === "skipped"
      ) {
        state = "done";
      }
      return {
        ...step,
        state,
        message: summarizeStep(step.id, {
          state,
          event: step.event,
          invoiceData,
          validation,
          approval,
          payment,
          issues,
        }),
      };
    });
  }, [timeline, issues, runState?.status, invoiceData, validation, approval, payment]);

  const canManualReview = useMemo(() => {
    if (!runState || runState.status !== "completed" || !runState.result) return false;
    const finalStatus = String(runState.result.final_status || "");
    return finalStatus === "HUMAN_REVIEW_REQUIRED" || finalStatus === "REJECTED";
  }, [runState]);

  useEffect(() => {
    if (!runId) return;
    setReviewer("");
    setReviewReason("");
    setReviewMessage("");
  }, [runId]);

  useEffect(() => {
    if (previewUrl.startsWith("blob:")) {
      URL.revokeObjectURL(previewUrl);
    }
    setPreviewUrl("");
    setPreviewError("");
    setIsPreviewOpen(false);
  }, [invoicePath]);

  useEffect(() => {
    if (!isPreviewOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") setIsPreviewOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isPreviewOpen]);

  const onRun = async () => {
    setIsStarting(true);
    setError("");
    setRunState(null);
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invoice_path: invoicePath })
      });
      if (!res.ok) {
        const body = await res.json();
        throw new Error(body.detail || "Failed to start run");
      }
      const data = await res.json();
      setRunId(data.run_id);
    } catch (err) {
      setError(String(err));
    } finally {
      setIsStarting(false);
    }
  };

  const uploadFile = async (file) => {
    if (!file) return;
    setError("");
    setIsUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch("/api/upload", {
        method: "POST",
        body,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Upload failed");
      }
      setInvoicePath(data.invoice_path);
      await loadInvoices();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsUploading(false);
    }
  };

  const onPreview = async () => {
    if (!invoicePath) return;
    setPreviewError("");
    setIsPreviewOpen(true);
    setIsPreviewLoading(true);
    try {
      const res = await fetch(`/api/preview_pdf?path=${encodeURIComponent(invoicePath)}`);
      if (!res.ok) {
        let message = "Preview unavailable.";
        try {
          const body = await res.json();
          message = body.detail || message;
        } catch (_err) {
          // ignore fallback parse errors
        }
        throw new Error(message);
      }
      const blob = await res.blob();
      const nextUrl = URL.createObjectURL(blob);
      if (previewUrl.startsWith("blob:")) {
        URL.revokeObjectURL(previewUrl);
      }
      setPreviewUrl(nextUrl);
    } catch (err) {
      setPreviewUrl("");
      setPreviewError(String(err));
      setIsPreviewOpen(false);
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const submitManualReview = async (action) => {
    if (!runId) return;
    setReviewMessage("");
    setError("");
    if (!reviewer.trim() || !reviewReason.trim()) {
      setReviewMessage("Reviewer and reason are required.");
      return;
    }
    setIsSubmittingReview(true);
    try {
      const res = await fetch(`/api/runs/${runId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          reviewer: reviewer.trim(),
          reason: reviewReason.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to apply manual review action.");
      }
      setRunState(data.run);
      setReviewMessage(
        action === "approve_and_pay"
          ? "Manual approval applied and payment executed."
          : "Manual rejection applied.",
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setIsSubmittingReview(false);
    }
  };

  const onDrop = async (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) await uploadFile(file);
  };

  const formatMoney = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
      Number(value),
    );
  };

  const formatSignedMoney = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
    const num = Number(value);
    const abs = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
      Math.abs(num),
    );
    if (num > 0) return `+${abs}`;
    if (num < 0) return `-${abs}`;
    return abs;
  };

  return (
    <div className="page">
      <header>
        <h1>Invoice Operations Workbench</h1>
        <p>
          End-to-end invoice intake, validation, approval, and payment with AI-assisted decisioning
          and audit-ready controls.
        </p>
      </header>

      <section className="card">
        <h2>New Run</h2>
        <label htmlFor="invoice">Invoice file</label>
        <div
          className={`dropzone ${dragOver ? "dropzone-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          <div>
            <strong>Drag and drop invoice file</strong> (.txt, .json, .csv, .xml, .pdf)
          </div>
          <div className="muted">or use file picker</div>
          <label className="upload-btn">
            {isUploading ? "Uploading..." : "Choose File"}
            <input
              type="file"
              accept=".txt,.json,.csv,.xml,.pdf"
              disabled={isUploading}
              onChange={(e) => uploadFile(e.target.files?.[0])}
            />
          </label>
        </div>
        <div className="row">
          <select
            id="invoice"
            value={invoicePath}
            onChange={(e) => setInvoicePath(e.target.value)}
          >
            {invoices.map((inv) => (
              <option key={inv} value={inv}>
                {inv}
              </option>
            ))}
          </select>
          <button
            className="button-secondary"
            disabled={!invoicePath || isPreviewLoading}
            onClick={onPreview}
          >
            {isPreviewLoading ? "Loading Preview..." : "Preview PDF"}
          </button>
          <button disabled={isStarting || !invoicePath} onClick={onRun}>
            {isStarting ? "Starting..." : "Run Pipeline"}
          </button>
        </div>
        {previewError && <p className="error">{previewError}</p>}
        {error && <p className="error">{error}</p>}
      </section>

      {isPreviewOpen && (
        <div className="preview-modal-overlay" onClick={() => setIsPreviewOpen(false)}>
          <div className="preview-modal" onClick={(e) => e.stopPropagation()}>
            <div className="preview-modal-head">
              <strong>PDF Preview</strong>
              <button className="button-secondary" onClick={() => setIsPreviewOpen(false)}>
                Close
              </button>
            </div>
            <p className="muted preview-modal-subtitle">
              Confirm this is the correct invoice before running.
            </p>
            {isPreviewLoading && <p className="muted">Loading preview...</p>}
            {!isPreviewLoading && previewUrl && (
              <iframe title="Pre-run PDF preview" src={previewUrl} className="preview-modal-viewer" />
            )}
          </div>
        </div>
      )}

      <section className="card">
        <div className="row spread">
          <h2>Run Status</h2>
          <StatusPill status={runState?.status} />
        </div>
        <div className="progress-bar">
          {steps.map((step) => (
            <div key={step.id} className="progress-segment">
              <div className={`progress-node progress-${step.state}`}>{step.label}</div>
            </div>
          ))}
        </div>
        {runId && <p className="muted">Run ID: {runId}</p>}
        {runState?.error && <p className="error">Error: {runState.error}</p>}
        {runState?.summary && (
          <div className="summary">
            <div>Invoice: {runState.summary.invoice_id}</div>
            <div>Decision: {runState.summary.decision}</div>
            <div>Final: {runState.summary.final_status}</div>
            <div>Issues: {runState.summary.issue_count}</div>
          </div>
        )}
      </section>

      {canManualReview && (
        <section className="card">
          <h2>Manual Review Action</h2>
          <p className="muted">
            This invoice requires manual intervention. You can override the automated outcome for
            this run by approving payment or confirming rejection.
          </p>
          <div className="review-grid">
            <label>
              Reviewer
              <input
                type="text"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                placeholder="Your name or reviewer id"
              />
            </label>
            <label>
              Reason
              <textarea
                value={reviewReason}
                onChange={(e) => setReviewReason(e.target.value)}
                placeholder="Why are you overriding this decision?"
                rows={3}
              />
            </label>
          </div>
          <div className="row">
            <button
              disabled={isSubmittingReview}
              onClick={() => submitManualReview("approve_and_pay")}
            >
              {isSubmittingReview ? "Submitting..." : "Approve and Pay"}
            </button>
            <button
              className="button-secondary"
              disabled={isSubmittingReview}
              onClick={() => submitManualReview("reject")}
            >
              {isSubmittingReview ? "Submitting..." : "Reject"}
            </button>
          </div>
          {reviewMessage && <p className="muted">{reviewMessage}</p>}
        </section>
      )}

      <section className="card">
        <h2>Decision Workspace</h2>
        <div className="tabs">
          <button
            className={activeTab === "timeline" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("timeline")}
          >
            Overview
          </button>
          <button
            className={activeTab === "pricing" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("pricing")}
          >
            Details
          </button>
          <button
            className={activeTab === "flags" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("flags")}
          >
            Flags ({issues.length})
          </button>
          <button
            className={activeTab === "guide" ? "tab tab-active" : "tab"}
            onClick={() => setActiveTab("guide")}
          >
            Policy Guide
          </button>
        </div>

        {activeTab === "timeline" && (
          <>
            <h2>Process Summary</h2>
            {timeline.length === 0 && <p className="muted">No events yet.</p>}
            <ul className="timeline">
              {steps.map((step) => (
                <li key={step.id}>
                  <div className="stage-row">
                    <div className="stage">{step.label}</div>
                    <span className={`mini-pill mini-${step.state}`}>{step.state}</span>
                  </div>
                  <div>{step.message}</div>
                </li>
              ))}
            </ul>
          </>
        )}

        {activeTab === "pricing" && (
          <>
            <h2>Invoice Details</h2>
            {!result && <p className="muted">Run a pipeline to see pricing details.</p>}
            {result && (
              <div className="details-grid">
                <div className="detail-card">
                  <h3>Invoice</h3>
                  <div>Invoice ID: {invoiceData?.invoice_id || "N/A"}</div>
                  <div>Vendor: {invoiceData?.vendor || "N/A"}</div>
                  <div>Source type: {invoiceData?.source_type || "N/A"}</div>
                  <div>Total amount: {formatMoney(invoiceData?.amount)}</div>
                </div>
                <div className="detail-card">
                  <h3>Dates & Terms</h3>
                  <div>Invoice date: {invoiceData?.date || "N/A"}</div>
                  <div>Due date: {invoiceData?.due_date || invoiceData?.due_date_raw || "N/A"}</div>
                  <div>Payment terms: {invoiceData?.payment_terms || "N/A"}</div>
                  <div>Notes: {invoiceData?.notes || "N/A"}</div>
                </div>
                <div className="detail-card">
                  <h3>Totals Reconciliation</h3>
                  <div>Invoice reported total: {formatMoney(invoiceData?.amount)}</div>
                  <div>
                    Calculated from line items:{" "}
                    {formatMoney(validation?.totals_check?.computed_line_total)}
                  </div>
                  <div>Variance (invoice - calculated): {formatSignedMoney(validation?.totals_check?.difference)}</div>
                  <div>
                    Result:{" "}
                    {Number(validation?.totals_check?.difference ?? 0) === 0
                      ? "Totals match."
                      : "Totals differ and should be reviewed."}
                  </div>
                </div>
                <div className="detail-card">
                  <h3>Approval</h3>
                  <div>Decision: {approval?.decision || "N/A"}</div>
                  <div>Policy flags: {(approval?.policy_flags ?? []).join(", ") || "None"}</div>
                  <div>Flag count: {(approval?.policy_flags ?? []).length}</div>
                </div>
              </div>
            )}

            {result && (
              <div className="detail-card detail-items">
                <h3>Line Items</h3>
                {(!invoiceData?.items || invoiceData.items.length === 0) && (
                  <p className="muted">No line items were extracted.</p>
                )}
                {invoiceData?.items?.length > 0 && (
                  <table className="items-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th>Quantity</th>
                        <th>Unit Price</th>
                        <th>Line Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoiceData.items.map((item, idx) => {
                        const qty = Number(item.quantity ?? 0);
                        const unit = Number(item.unit_price ?? 0);
                        const computed = qty * unit;
                        const line = item.line_total ?? computed;
                        return (
                          <tr key={`${item.item}-${idx}`}>
                            <td>{item.raw_item || item.item || "N/A"}</td>
                            <td>{qty}</td>
                            <td>{formatMoney(unit)}</td>
                            <td>{formatMoney(line)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {result && (
              <div className="detail-card detail-items">
                <h3>Inventory Check</h3>
                {inventoryRows.length === 0 && (
                  <p className="muted">No inventory checks available.</p>
                )}
                {inventoryRows.length > 0 && (
                  <table className="items-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th>Requested</th>
                        <th>DB Stock</th>
                        <th>Delta</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {inventoryRows.map((row) => (
                        <tr key={row.id}>
                          <td>{row.item}</td>
                          <td>{row.requested}</td>
                          <td>{row.stock === null ? "N/A" : row.stock}</td>
                          <td>
                            {row.delta === null
                              ? "N/A"
                              : row.delta > 0
                              ? `+${row.delta}`
                              : row.delta}
                          </td>
                          <td>
                            <span className={`status-badge status-${row.tone}`}>{row.label}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {result && (
              <div className="detail-card detail-items">
                <h3>Source Document</h3>
                {sourcePath && (
                  <div className="muted source-path">File: {sourcePath}</div>
                )}
                {sourceExt === "pdf" ? (
                  <iframe
                    title="Invoice PDF Preview"
                    src={sourceUrl}
                    className="source-viewer"
                  />
                ) : (
                  <pre className="source-text">
                    {invoiceData?.raw_text?.trim()
                      ? invoiceData.raw_text
                      : "Raw source preview unavailable for this file."}
                  </pre>
                )}
              </div>
            )}

            {result && (
              <div className="detail-card detail-items">
                <h3>Outcome</h3>
                <div>Final status: {runState?.summary?.final_status || "N/A"}</div>
                <div>Total flags: {issues.length}</div>
                <div>Highest severity: {highestSeverity}</div>
                <div>Requires review: {String((approval?.decision || "") !== "APPROVE")}</div>
                <div>Payment status: {payment?.status || "N/A"}</div>
                <div>Transaction: {payment?.transaction_id || "N/A"}</div>
                <div>
                  Paid amount: {payment?.status === "success" ? formatMoney(invoiceData?.amount) : "N/A"}
                </div>
              </div>
            )}

            {result && (
              <div className="detail-card detail-items">
                <h3>Approval Rationale</h3>
                <div>{approvalNarrative}</div>
                <div>
                  Policy flags detail: {(approval?.policy_flags ?? []).join(", ") || "None"}
                </div>
              </div>
            )}
          </>
        )}

        {activeTab === "flags" && (
          <>
            <h2>Flags</h2>
            {issues.length === 0 && <p className="muted">No flags for this run.</p>}
            {issueGroups.map(([stage, stageIssues]) => (
              <div key={stage} className="flag-group">
                <h3>{stage}</h3>
                <ul className="flags">
                  {stageIssues.map((issue, idx) => (
                    <li key={`${issue.code}-${idx}`}>
                      <div>
                        <strong>{issue.code}</strong> <span className="muted">({issue.severity})</span>
                      </div>
                      <div>{issue.message}</div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </>
        )}

        {activeTab === "guide" && (
          <>
            <h2>How Decisions Work</h2>
            <p className="muted">
              This guide explains how validation and approval signals map to APPROVE, HUMAN_REVIEW,
              or REJECT outcomes.
            </p>

            <div className="guide-grid">
              <div className="detail-card">
                <h3>Decision Matrix</h3>
                <table className="items-table">
                  <thead>
                    <tr>
                      <th>Condition</th>
                      <th>Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Any critical issue (integrity or high risk)</td>
                      <td>REJECT</td>
                    </tr>
                    <tr>
                      <td>Error-level issue or validation fails</td>
                      <td>HUMAN_REVIEW</td>
                    </tr>
                    <tr>
                      <td>Ambiguity / uncertainty / parse concerns</td>
                      <td>HUMAN_REVIEW</td>
                    </tr>
                    <tr>
                      <td>Amount exceeds VP threshold</td>
                      <td>HUMAN_REVIEW</td>
                    </tr>
                    <tr>
                      <td>No policy escalations and checks pass</td>
                      <td>APPROVE</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="detail-card">
                <h3>Severity Legend</h3>
                <div><strong>critical</strong>: hard-stop risk or integrity break; reject path.</div>
                <div><strong>error</strong>: significant mismatch; manual review required.</div>
                <div><strong>warning</strong>: uncertainty or policy concern; usually review.</div>
                <div><strong>info</strong>: non-blocking context for auditability.</div>
              </div>

              <div className="detail-card">
                <h3>Critical Flags - Reject</h3>
                <div><strong>VAL_OUT_OF_STOCK</strong>: item stock is zero.</div>
                <div><strong>VAL_INVALID_QUANTITY</strong>: invalid quantity (for example negative).</div>
                <div>
                  Policy mapping: any <strong>critical</strong> issue triggers
                  <strong> REJECT</strong>.
                </div>
              </div>

              <div className="detail-card">
                <h3>Error / Warning / Uncertainty - Human Review</h3>
                <div><strong>VAL_STOCK_MISMATCH</strong> (error): requested exceeds stock.</div>
                <div><strong>VAL_UNKNOWN_ITEM</strong> (error): item not found in DB.</div>
                <div><strong>VAL_AMBIGUOUS_ITEM_MATCH</strong> (warning): fuzzy match needs manual mapping.</div>
                <div><strong>VAL_DUE_DATE_RELATIVE_LANGUAGE</strong> (warning): due date not deterministic.</div>
                <div><strong>VAL_TERMS_DUE_MISMATCH</strong> (warning): terms and due date conflict.</div>
                <div><strong>uncertainty_requires_review</strong>: policy escalation for ambiguity.</div>
                <div><strong>vp_threshold_exceeded</strong>: high-value invoice escalation.</div>
                <div><strong>payment_pressure_language_detected</strong>: urgent pressure wording detected.</div>
                <div><strong>nonstandard_payment_instruction</strong>: risky payment instructions (e.g., wire/crypto).</div>
                <div>
                  Policy mapping: any <strong>error</strong> or unresolved uncertainty routes to
                  <strong> HUMAN_REVIEW</strong>.
                </div>
              </div>
            </div>

            <div className="detail-card detail-items">
              <h3>What To Do Next</h3>
              <div><strong>APPROVE</strong>: proceed; payment is executed automatically.</div>
              <div><strong>HUMAN_REVIEW</strong>: route to reviewer for confirmation before payment.</div>
              <div><strong>REJECT</strong>: stop processing and investigate risk/integrity failure.</div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

export default App;
