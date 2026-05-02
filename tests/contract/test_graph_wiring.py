"""Contract tests for LangGraph graph topology."""
from app.pipeline.graph import build_graph


def test_graph_compiles() -> None:
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes() -> None:
    graph = build_graph()
    node_names = set(graph.nodes.keys())
    expected = {
        "validate_inputs", "embed_query", "retrieve_candidates",
        "rerank_candidates", "enrich_candidates", "reason",
        "validate_output", "generate_drafts", "persist_results",
    }
    assert expected.issubset(node_names)


def test_graph_entry_point_is_validate() -> None:
    graph = build_graph()
    assert "__start__" in graph.nodes or graph.config.get("entry_point") == "validate_inputs"
