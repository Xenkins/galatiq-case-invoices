from __future__ import annotations

from app.schemas.models import PipelineState


def new_pipeline_state(invoice_path: str) -> PipelineState:
    return PipelineState(invoice_path=invoice_path)
