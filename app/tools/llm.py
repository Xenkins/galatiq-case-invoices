from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


SYSTEM_JSON_ONLY = (
    "You are a strict JSON API. Return only valid JSON. "
    "Do not include markdown, code fences, or extra commentary."
)


def build_grok_client(api_key: Optional[str] = None) -> Any:
    resolved_key = api_key or os.getenv("GROK_API_KEY")
    if not resolved_key:
        return None
    try:
        from xai_sdk import Client  # type: ignore
    except Exception:
        return None
    return Client(api_key=resolved_key)


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return json.loads(text[first : last + 1])
    raise ValueError("No JSON object found in LLM response.")


def _chat_json(
    client: Any,
    *,
    model: str,
    user_prompt: str,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    if client is None:
        raise ValueError("Grok client is not available.")
    from xai_sdk import chat  # type: ignore

    exchange = client.chat.create(
        model=model,
        messages=[
            chat.system(SYSTEM_JSON_ONLY),
            chat.user(user_prompt),
        ],
        temperature=temperature,
        response_format="json_object",
    )
    response = exchange.sample()
    content = getattr(response, "content", "")
    if not content:
        content = "{}"
    return _extract_json(content)


def extract_invoice_with_llm(
    client: Any,
    *,
    model: str,
    raw_text: str,
    source_type: str,
) -> Dict[str, Any]:
    prompt = f"""
Parse this invoice into normalized JSON.

Input type: {source_type}
Return this schema exactly:
{{
  "invoice_id": "<string or empty>",
  "vendor": "<string or empty>",
  "date": "<YYYY-MM-DD or empty>",
  "due_date": "<YYYY-MM-DD, relative text, or empty>",
  "due_date_raw": "<raw due date text or empty>",
  "payment_terms": "<string or empty>",
  "notes": "<string or empty>",
  "amount": <number or null>,
  "items": [
    {{
      "item": "<string>",
      "quantity": <integer>,
      "unit_price": <number or null>,
      "line_total": <number or null>
    }}
  ]
}}

Normalize obvious OCR issues (e.g. letter O in numbers). Use null where unknown.

Invoice text:
{raw_text}
"""
    return _chat_json(client, model=model, user_prompt=prompt, temperature=0.0)


def reflect_stage_with_llm(
    client: Any,
    *,
    model: str,
    stage: str,
    checklist: List[str],
    stage_input: Dict[str, Any],
    stage_output: Dict[str, Any],
) -> Dict[str, Any]:
    prompt = f"""
You are reviewing a {stage} agent output.
Evaluate against checklist and return strict JSON:
{{
  "status": "pass|retry|fail",
  "feedback": "<short reason>",
  "confidence": <0.0 to 1.0>,
  "checks": ["<completed checks>"]
}}

Checklist:
{json.dumps(checklist, indent=2)}

Stage input:
{json.dumps(stage_input, indent=2)}

Stage output:
{json.dumps(stage_output, indent=2)}
"""
    return _chat_json(client, model=model, user_prompt=prompt, temperature=0.0)


def generate_approval_rationale_with_llm(
    client: Any,
    *,
    model: str,
    decision: str,
    policy_flags: List[str],
    invoice_summary: Dict[str, Any],
    issues: List[Dict[str, Any]],
) -> str:
    prompt = f"""
Write a concise approval rationale in 2-4 sentences for an invoice workflow.
Decision: {decision}
Policy flags: {policy_flags}
Invoice summary: {json.dumps(invoice_summary, indent=2)}
Issues: {json.dumps(issues, indent=2)}

Return strict JSON:
{{
  "rationale": "<text>"
}}
"""
    result = _chat_json(client, model=model, user_prompt=prompt, temperature=0.1)
    rationale = str(result.get("rationale") or "").strip()
    return rationale
