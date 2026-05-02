"""LangGraph StateGraph assembly for the JobMatch pipeline."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.pipeline.nodes.draft import draft_invites
from app.pipeline.nodes.embed import embed_query
from app.pipeline.nodes.enrich import enrich_candidates
from app.pipeline.nodes.persist import persist_results
from app.pipeline.nodes.reason import reason_agent
from app.pipeline.nodes.rerank import rerank_candidates
from app.pipeline.nodes.retrieve import retrieve_candidates
from app.pipeline.nodes.validate import validate_inputs
from app.pipeline.nodes.verify import validate_output
from app.pipeline.state import PipelineState


def _route_after_verify(state: PipelineState) -> str:
    errors = state.get("validation_errors", [])
    loop_count = state.get("loop_count", 0)
    if errors and loop_count <= 1:
        return "reason"
    return "generate_drafts"


def _route_after_validate(state: PipelineState) -> str:
    if state.get("error"):
        return END
    return "embed_query"


def build_graph() -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("validate_inputs", validate_inputs)
    builder.add_node("embed_query", embed_query)
    builder.add_node("retrieve_candidates", retrieve_candidates)
    builder.add_node("rerank_candidates", rerank_candidates)
    builder.add_node("enrich_candidates", enrich_candidates)
    builder.add_node("reason", reason_agent)
    builder.add_node("validate_output", validate_output)
    builder.add_node("generate_drafts", draft_invites)
    builder.add_node("persist_results", persist_results)

    builder.set_entry_point("validate_inputs")

    builder.add_conditional_edges(
        "validate_inputs",
        _route_after_validate,
        {"embed_query": "embed_query", END: END},
    )
    builder.add_edge("embed_query", "retrieve_candidates")
    builder.add_edge("retrieve_candidates", "rerank_candidates")
    builder.add_edge("rerank_candidates", "enrich_candidates")
    builder.add_edge("enrich_candidates", "reason")
    builder.add_edge("reason", "validate_output")
    builder.add_conditional_edges(
        "validate_output",
        _route_after_verify,
        {"reason": "reason", "generate_drafts": "generate_drafts"},
    )
    builder.add_edge("generate_drafts", "persist_results")
    builder.add_edge("persist_results", END)

    return builder.compile()
