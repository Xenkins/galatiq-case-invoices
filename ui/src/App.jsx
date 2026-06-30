import { useEffect, useMemo, useState } from "react";

const POLL_MS = 900;

function StatusPill({ status }) {
  const cls =
    status === "completed"
      ? "pill pill-ok"
      : status === "failed"
      ? "pill pill-err"
      : "pill pill-run";
  return <span className={cls}>{status ?? "idle"}</span>;
}

function App() {
  const [invoices, setInvoices] = useState([]);
  const [invoicePath, setInvoicePath] = useState("data/invoices/invoice_1001.txt");
  const [runId, setRunId] = useState(null);
  const [runState, setRunState] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadInvoices() {
      const res = await fetch("/api/invoices");
      const data = await res.json();
      setInvoices(data.invoices ?? []);
      if ((data.invoices ?? []).length > 0) {
        setInvoicePath((prev) => prev || data.invoices[0]);
      }
    }
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

  return (
    <div className="page">
      <header>
        <h1>Invoice Orchestration Monitor</h1>
        <p>LangGraph + Grok multi-agent pipeline visualization</p>
      </header>

      <section className="card">
        <label htmlFor="invoice">Invoice file</label>
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
          <button disabled={isStarting || !invoicePath} onClick={onRun}>
            {isStarting ? "Starting..." : "Run Pipeline"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <div className="row spread">
          <h2>Run Status</h2>
          <StatusPill status={runState?.status} />
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

      <section className="card">
        <h2>Stage Timeline</h2>
        {timeline.length === 0 && <p className="muted">No events yet.</p>}
        <ul className="timeline">
          {timeline.map((event, idx) => (
            <li key={`${event.timestamp}-${idx}`}>
              <div className="stage">{event.stage}</div>
              <div className="stamp">{event.timestamp}</div>
              <div>{event.summary}</div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default App;
