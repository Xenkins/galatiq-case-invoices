from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.graph.builder import pretty_result, run_invoice_pipeline


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
load_dotenv(dotenv_path=ROOT / ".env")

if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")


def _append_event(run_id: str, event: Dict[str, Any]) -> None:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            return
        run.events.append(event)


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
    invoice_dir = ROOT / "data" / "invoices"
    files = sorted(str(path.relative_to(ROOT)) for path in invoice_dir.glob("*") if path.is_file())
    return {"invoices": files}


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
