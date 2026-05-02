"""parse_missing_fields tool — re-parses specific fields from resume text via Groq."""
from __future__ import annotations

import json
import logging

from app.pipeline.state import PipelineState
from app.pipeline.tools import ToolResult
from app.services import llm

logger = logging.getLogger(__name__)


async def parse_missing_fields(
    state: PipelineState,
    candidate_id: str,
    fields: list[str],
) -> ToolResult:
    enriched = state.get("enriched_candidates", [])
    candidate = next((c for c in enriched if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        return ToolResult(success=False, data=None, error=f"candidate {candidate_id} not found")

    prompt = (
        f"Extract only these fields from the resume: {', '.join(fields)}.\n\n"
        f"Resume:\n{candidate['resume_text'][:4000]}\n\n"
        f"Respond ONLY with JSON: {{ {', '.join(f'\"{f}\": value' for f in fields)} }}"
    )
    try:
        response = await llm.chat_completion(
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            tenant_id=state["tenant_id"],
            run_id=state["run_id"],
            max_tokens=256,
            temperature=0.0,
        )
        extracted = json.loads(response.choices[0].message.content)
        return ToolResult(success=True, data={"candidate_id": candidate_id, "fields": extracted})
    except Exception as exc:
        logger.warning("parse_missing_fields failed candidate=%s error=%s", candidate_id, exc)
        return ToolResult(success=False, data=None, error=str(exc))
