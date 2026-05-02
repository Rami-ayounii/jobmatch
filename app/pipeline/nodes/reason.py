"""reason pipeline node: rubric derivation + bounded Claude Sonnet agent loop."""
from __future__ import annotations

import importlib.resources
import json
import logging
import time
from typing import Any

from app.pipeline.state import EnrichedCandidate, PipelineState, ShortlistEntry
from app.pipeline.tools.candidate import get_candidate_profile
from app.pipeline.tools.finalize import finalize_shortlist
from app.pipeline.tools.more import request_more_candidates
from app.pipeline.tools.parse import parse_missing_fields
from app.pipeline.tools.rescore import rescore_candidate
from app.services import llm

logger = logging.getLogger(__name__)

_REASONER_MODEL = "anthropic/claude-sonnet-4-6"
_RUBRIC_MODEL = "groq/llama-3.1-8b-instant"
_MAX_TOOL_CALLS = 6
_COST_CAP_USD = 1.50
_TIME_CAP_S = 45.0

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_candidate_profile",
            "description": "Get full profile and enriched fields for a candidate.",
            "parameters": {
                "type": "object",
                "properties": {"candidate_id": {"type": "string"}},
                "required": ["candidate_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_missing_fields",
            "description": "Re-parse specific fields from a candidate's resume using AI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["candidate_id", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rescore_candidate",
            "description": "Record a score for a candidate on a specific criterion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "criterion": {"type": "string"},
                    "score": {"type": "number"},
                    "justification": {"type": "string"},
                },
                "required": ["candidate_id", "criterion", "score", "justification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_more_candidates",
            "description": "Request additional candidates from the retrieval pool.",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_shortlist",
            "description": "Submit your final ranked shortlist of exactly 5 candidates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "rank": {"type": "integer"},
                                "candidate_id": {"type": "string"},
                                "score": {"type": "number"},
                                "justification": {"type": "string"},
                                "citations": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["rank", "candidate_id", "score", "justification", "citations"],
                        },
                    }
                },
                "required": ["candidates"],
            },
        },
    },
]


def _load_prompt(name: str) -> str:
    return (
        importlib.resources.files("app.pipeline.nodes.prompts")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _candidate_summary(c: EnrichedCandidate) -> str:
    ef = c["enriched_fields"]
    skills = c["structured_jsonb"].get("skills", [])[:5]
    years = c["structured_jsonb"].get("experience_years") or ef.get("years_experience", "?")
    summary = ef.get("summary", "")
    return (
        f"ID: {c['candidate_id']}\n"
        f"Rerank score: {c['rerank_score']:.3f}\n"
        f"Years exp: {years}\n"
        f"Skills: {', '.join(skills)}\n"
        f"Summary: {summary}\n"
        f"Resume excerpt: {c['resume_text'][:500]}"
    )


def _extract_cost(response: Any) -> float:
    try:
        import litellm
        return litellm.completion_cost(completion_response=response)
    except Exception:
        usage = getattr(response, "usage", None)
        if usage:
            return (getattr(usage, "prompt_tokens", 0) * 3e-6 +
                    getattr(usage, "completion_tokens", 0) * 15e-6)
        return 0.0


async def _derive_rubric(state: PipelineState) -> tuple[dict[str, Any], float]:
    prompt = _load_prompt("rubric_derivation.txt").replace(
        "{job_description}", state["job_description"][:3000]
    )
    response = await llm.chat_completion(
        model=_RUBRIC_MODEL,
        messages=[{"role": "user", "content": prompt}],
        tenant_id=state["tenant_id"],
        run_id=state["run_id"],
        max_tokens=512,
        temperature=0.0,
    )
    cost = _extract_cost(response)
    try:
        rubric = json.loads(response.choices[0].message.content)
    except Exception:
        rubric = {"criteria": []}
    return rubric, cost


async def _dispatch_tool(
    state: PipelineState,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "get_candidate_profile":
        result = get_candidate_profile(state, **args)
    elif tool_name == "rescore_candidate":
        result = rescore_candidate(state, **args)
    elif tool_name == "request_more_candidates":
        result = request_more_candidates(state, **args)
    elif tool_name == "finalize_shortlist":
        result = finalize_shortlist(state, **args)
    elif tool_name == "parse_missing_fields":
        result = await parse_missing_fields(state, **args)
    else:
        from app.pipeline.tools import ToolResult
        result = ToolResult(success=False, data=None, error=f"unknown tool: {tool_name}")
    return result.model_dump()


async def reason_agent(state: PipelineState) -> PipelineState:
    start_time = time.monotonic()
    trace: list[dict[str, Any]] = list(state.get("agent_trace", []))
    total_cost = state.get("total_cost_usd", 0.0)

    rubric, rubric_cost = await _derive_rubric(state)
    total_cost += rubric_cost
    trace.append({"type": "rubric_derived", "rubric": rubric, "cost_usd": rubric_cost})

    system_prompt = _load_prompt("reason_system.txt")
    candidates_text = "\n\n---\n\n".join(
        _candidate_summary(c) for c in state.get("enriched_candidates", [])
    )
    user_message = (
        f"Job Title: {state.get('job_title', '')}\n"
        f"Job Description:\n{state.get('job_description', '')[:2000]}\n\n"
        f"Scoring Rubric:\n{json.dumps(rubric, indent=2)}\n\n"
        f"Candidates ({len(state.get('enriched_candidates', []))}):\n\n{candidates_text}"
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    tool_call_count = 0
    shortlist: list[ShortlistEntry] | None = None

    while tool_call_count < _MAX_TOOL_CALLS:
        elapsed = time.monotonic() - start_time
        if elapsed >= _TIME_CAP_S:
            trace.append({"type": "timeout", "elapsed_s": elapsed})
            break
        if total_cost >= _COST_CAP_USD:
            trace.append({"type": "cost_cap_hit", "total_cost_usd": total_cost})
            break

        response = await llm.chat_completion(
            model=_REASONER_MODEL,
            messages=messages,
            tenant_id=state["tenant_id"],
            run_id=state["run_id"],
            max_tokens=4096,
            temperature=0.0,
            tools=_TOOL_SCHEMAS,
            tool_choice="auto",
        )
        call_cost = _extract_cost(response)
        total_cost += call_cost
        trace.append({"type": "llm_call", "model": _REASONER_MODEL, "cost_usd": call_cost})

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            break

        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ]})

        for tc in tool_calls:
            tool_call_count += 1
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            tool_result = await _dispatch_tool(state, tool_name, args)
            trace.append({
                "type": "tool_call",
                "call_index": tool_call_count,
                "tool_name": tool_name,
                "args": args,
                "result": tool_result,
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result),
            })

            if tool_name == "finalize_shortlist" and tool_result.get("success"):
                shortlist = tool_result["data"]["shortlist"]
                break

        if shortlist is not None:
            break

    if shortlist is None:
        enriched = state.get("enriched_candidates", [])
        shortlist = [
            ShortlistEntry(
                rank=i + 1,
                candidate_id=c["candidate_id"],
                score=c["rerank_score"],
                justification="Selected by ranking score (agent did not finalize).",
                citations=[],
            )
            for i, c in enumerate(enriched[:5])
        ]
        trace.append({"type": "fallback_shortlist", "reason": "agent_did_not_finalize"})

    return {
        **state,
        "shortlist": shortlist,
        "rubric": rubric,
        "agent_trace": trace,
        "total_cost_usd": total_cost,
    }
