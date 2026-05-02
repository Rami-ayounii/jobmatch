"""enrich_candidates pipeline node: parallel Groq re-parse of top-15 candidates."""
from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging

from app.pipeline.state import EnrichedCandidate, PipelineState, RankedCandidate
from app.services import llm

logger = logging.getLogger(__name__)


def _load_prompt() -> str:
    return (
        importlib.resources.files("app.pipeline.nodes.prompts")
        .joinpath("enrich.txt")
        .read_text(encoding="utf-8")
    )


async def _enrich_one(
    candidate: RankedCandidate,
    run_id: str,
    tenant_id: str,
) -> EnrichedCandidate:
    prompt = _load_prompt().replace("{resume_text}", candidate["resume_text"][:4000])
    try:
        response = await llm.chat_completion(
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            tenant_id=tenant_id,
            run_id=run_id,
            max_tokens=512,
            temperature=0.0,
        )
        enriched_fields = json.loads(response.choices[0].message.content)
    except Exception as exc:
        logger.warning("enrich_one failed candidate=%s error=%s", candidate["candidate_id"], exc)
        enriched_fields = {}

    return EnrichedCandidate(
        candidate_id=candidate["candidate_id"],
        resume_text=candidate["resume_text"],
        structured_jsonb=candidate["structured_jsonb"],
        rerank_score=candidate["rerank_score"],
        enriched_fields=enriched_fields,
    )


async def enrich_candidates(state: PipelineState) -> PipelineState:
    ranked = state.get("reranked_candidates", [])
    if not ranked:
        return {**state, "enriched_candidates": []}

    run_id = state["run_id"]
    tenant_id = state["tenant_id"]

    enriched = await asyncio.gather(
        *[_enrich_one(c, run_id, tenant_id) for c in ranked]
    )

    return {**state, "enriched_candidates": list(enriched)}
