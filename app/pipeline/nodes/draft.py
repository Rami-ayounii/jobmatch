"""draft_invites pipeline node: generate 5 personalized invite emails via Groq."""
from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging

from app.pipeline.state import EnrichedCandidate, InviteDraft, PipelineState, ShortlistEntry
from app.services import llm

logger = logging.getLogger(__name__)


def _load_prompt() -> str:
    return (
        importlib.resources.files("app.pipeline.nodes.prompts")
        .joinpath("draft_invite.txt")
        .read_text(encoding="utf-8")
    )


def _find_enriched(
    enriched: list[EnrichedCandidate], candidate_id: str
) -> EnrichedCandidate | None:
    return next((c for c in enriched if c["candidate_id"] == candidate_id), None)


async def _draft_one(
    entry: ShortlistEntry,
    enriched: list[EnrichedCandidate],
    job_title: str,
    job_description: str,
    run_id: str,
    tenant_id: str,
) -> InviteDraft:
    candidate = _find_enriched(enriched, entry["candidate_id"])
    seniority = (
        candidate["structured_jsonb"].get("seniority_level", "professional")
        if candidate else "professional"
    )
    prompt = (
        _load_prompt()
        .replace("{job_title}", job_title)
        .replace("{job_description_excerpt}", job_description[:500])
        .replace("{seniority_level}", seniority or "professional")
        .replace("{justification}", entry["justification"][:300])
    )
    try:
        response = await llm.chat_completion(
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            tenant_id=tenant_id,
            run_id=run_id,
            max_tokens=512,
            temperature=0.3,
        )
        draft = json.loads(response.choices[0].message.content)
        return InviteDraft(
            candidate_id=entry["candidate_id"],
            subject=draft["subject"],
            body=draft["body"],
        )
    except Exception as exc:
        logger.warning("draft_one failed candidate=%s error=%s", entry["candidate_id"], exc)
        return InviteDraft(
            candidate_id=entry["candidate_id"],
            subject=f"Interview Invitation - {job_title}",
            body=(
                "Dear Candidate,\n\n"
                "We would like to invite you to interview for this position.\n\n"
                "Best regards,\nThe Hiring Team"
            ),
        )


async def draft_invites(state: PipelineState) -> PipelineState:
    shortlist = state.get("shortlist", [])
    enriched = state.get("enriched_candidates", [])
    job_title = state.get("job_title", "")
    job_description = state.get("job_description", "")

    drafts = await asyncio.gather(
        *[
            _draft_one(entry, enriched, job_title, job_description, state["run_id"], state["tenant_id"])
            for entry in shortlist
        ]
    )

    return {**state, "draft_invites": list(drafts)}
