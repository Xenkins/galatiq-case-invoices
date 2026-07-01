from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.agents.approval import approval_agent
from app.agents.ingestion import ingestion_agent
from app.agents.payment import payment_agent
from app.agents.supervisor import supervisor_agent
from app.agents.validation import validation_agent
from app.graph.state import new_pipeline_state
from app.reflection.approval_reflect import approval_reflection
from app.reflection.ingestion_reflect import ingest_reflection
from app.reflection.validation_reflect import validation_reflection
from app.schemas.models import ReflectionResult
from app.tools.db import ensure_inventory_db
from app.tools.llm import build_grok_client


def _apply_reflection(
    state,
    stage_key: str,
    reflection: ReflectionResult,
    retry_callback,
):
    state.reflection_feedback[stage_key] = reflection.feedback
    state.log_stage(
        f"{stage_key}_reflect",
        "Completed reflection check.",
        {"status": reflection.status, "feedback": reflection.feedback, "checks": reflection.checks},
    )
    if reflection.status == "retry" and state.retry_counts[stage_key] < 1:
        state.retry_counts[stage_key] += 1
        state.log_stage(
            f"{stage_key}_reflect",
            "Retrying stage after reflection feedback.",
            {"retry_count": state.retry_counts[stage_key]},
        )
        return retry_callback(state)
    return state


def _run_ingestion_stage(state, *, llm_client: Any = None, grok_model: str = "grok-3"):
    state = ingestion_agent(state, llm_client=llm_client, grok_model=grok_model)
    ingestion_check = ingest_reflection(
        state,
        llm_client=llm_client,
        grok_model=grok_model,
    )
    if ingestion_check.status in {"retry", "fail"}:
        state = _apply_reflection(
            state,
            "ingest",
            ingestion_check,
            lambda current: ingestion_agent(
                current,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
        ingestion_check = ingest_reflection(
            state,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        state = _apply_reflection(
            state,
            "ingest",
            ingestion_check,
            lambda current: ingestion_agent(
                current,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
    else:
        state = _apply_reflection(
            state,
            "ingest",
            ingestion_check,
            lambda current: ingestion_agent(
                current,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
    return state, ingestion_check


def _run_validation_stage(
    state,
    db_path: str,
    *,
    llm_client: Any = None,
    grok_model: str = "grok-3",
):
    state = validation_agent(state, db_path=db_path)
    validation_check = validation_reflection(
        state,
        llm_client=llm_client,
        grok_model=grok_model,
    )
    if validation_check.status in {"retry", "fail"}:
        state = _apply_reflection(
            state,
            "validate",
            validation_check,
            lambda current: validation_agent(current, db_path=db_path),
        )
        validation_check = validation_reflection(
            state,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        state = _apply_reflection(
            state,
            "validate",
            validation_check,
            lambda current: validation_agent(current, db_path=db_path),
        )
    else:
        state = _apply_reflection(
            state,
            "validate",
            validation_check,
            lambda current: validation_agent(current, db_path=db_path),
        )
    return state, validation_check


def _run_approval_stage(
    state,
    vp_threshold: float,
    *,
    llm_client: Any = None,
    grok_model: str = "grok-3",
):
    state = approval_agent(
        state,
        vp_threshold=vp_threshold,
        llm_client=llm_client,
        grok_model=grok_model,
    )
    approval_check = approval_reflection(
        state,
        llm_client=llm_client,
        grok_model=grok_model,
    )
    if approval_check.status in {"retry", "fail"}:
        state = _apply_reflection(
            state,
            "approve",
            approval_check,
            lambda current: approval_agent(
                current,
                vp_threshold=vp_threshold,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
        approval_check = approval_reflection(
            state,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        state = _apply_reflection(
            state,
            "approve",
            approval_check,
            lambda current: approval_agent(
                current,
                vp_threshold=vp_threshold,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
    else:
        state = _apply_reflection(
            state,
            "approve",
            approval_check,
            lambda current: approval_agent(
                current,
                vp_threshold=vp_threshold,
                llm_client=llm_client,
                grok_model=grok_model,
            ),
        )
    return state, approval_check


def _run_pipeline_langgraph(
    invoice_path: str,
    db_path: str,
    vp_threshold: float,
    *,
    llm_client: Any = None,
    grok_model: str = "grok-3",
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    from langgraph.graph import END, START, StateGraph

    class GraphState(dict):
        pass

    cursor = {"idx": 0}

    def emit_new_events(state) -> None:
        if on_event is None:
            return
        while cursor["idx"] < len(state.audit_log):
            on_event(state.audit_log[cursor["idx"]])
            cursor["idx"] += 1

    def ingest_stage(graph_state: Dict[str, Any]) -> Dict[str, Any]:
        state = graph_state["state"]
        updated, _ = _run_ingestion_stage(
            state,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        emit_new_events(updated)
        return {"state": updated}

    def validate_stage(graph_state: Dict[str, Any]) -> Dict[str, Any]:
        state = graph_state["state"]
        updated, _ = _run_validation_stage(
            state,
            db_path=db_path,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        emit_new_events(updated)
        return {"state": updated}

    def approve_stage(graph_state: Dict[str, Any]) -> Dict[str, Any]:
        state = graph_state["state"]
        updated, _ = _run_approval_stage(
            state,
            vp_threshold=vp_threshold,
            llm_client=llm_client,
            grok_model=grok_model,
        )
        emit_new_events(updated)
        return {"state": updated}

    def payment_stage(graph_state: Dict[str, Any]) -> Dict[str, Any]:
        state = graph_state["state"]
        updated = payment_agent(state)
        emit_new_events(updated)
        return {"state": updated}

    def supervisor_stage(graph_state: Dict[str, Any]) -> Dict[str, Any]:
        state = graph_state["state"]
        updated = supervisor_agent(state)
        emit_new_events(updated)
        return {"state": updated}

    graph = StateGraph(dict)
    graph.add_node("ingest_stage", ingest_stage)
    graph.add_node("validate_stage", validate_stage)
    graph.add_node("approve_stage", approve_stage)
    graph.add_node("payment_stage", payment_stage)
    graph.add_node("supervisor_stage", supervisor_stage)

    graph.add_edge(START, "ingest_stage")
    graph.add_edge("ingest_stage", "validate_stage")
    graph.add_edge("validate_stage", "approve_stage")
    graph.add_edge("approve_stage", "payment_stage")
    graph.add_edge("payment_stage", "supervisor_stage")
    graph.add_edge("supervisor_stage", END)

    app = graph.compile()
    initial = {"state": new_pipeline_state(invoice_path)}
    result = app.invoke(initial)
    final_state = result["state"]
    return final_state.to_dict()


def run_invoice_pipeline(
    invoice_path: str,
    db_path: str = "inventory.db",
    vp_threshold: float = 10000.0,
    grok_api_key: str | None = None,
    grok_model: str = "grok-3",
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    ensure_inventory_db(db_path)
    llm_client = build_grok_client(grok_api_key)
    if llm_client is None:
        raise RuntimeError(
            "Grok client could not be initialized. Set GROK_API_KEY and ensure xai-sdk is installed."
        )
    return _run_pipeline_langgraph(
        invoice_path=invoice_path,
        db_path=db_path,
        vp_threshold=vp_threshold,
        llm_client=llm_client,
        grok_model=grok_model,
        on_event=on_event,
    )


def pretty_result(result: Dict[str, Any]) -> Dict[str, Any]:
    invoice = result.get("invoice_data") or {}
    validation = result.get("validation_result") or {}
    approval = result.get("approval_result") or {}
    payment = result.get("payment_result") or {}
    return {
        "invoice_id": invoice.get("invoice_id"),
        "vendor": invoice.get("vendor"),
        "amount": invoice.get("amount"),
        "validation_pass": validation.get("validation_pass"),
        "decision": approval.get("decision"),
        "payment_status": payment.get("status"),
        "final_status": result.get("final_status"),
        "needs_human_review": result.get("needs_human_review"),
        "issue_count": len(result.get("issues") or []),
    }
