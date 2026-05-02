"""Safety invariant: send_email must NOT be callable by the agent.

Per CLAUDE.md §8: 'No tool callable by the agent has side effects on
external systems.' Email sending happens in send_worker (post human
approval), never in the agent.
"""
from app.pipeline.nodes.reason import _TOOL_SCHEMAS


def test_send_email_not_in_agent_tools() -> None:
    """Verify send_email is not registered in the agent's tool palette."""
    tool_names = {
        schema["function"]["name"]
        for schema in _TOOL_SCHEMAS
        if schema.get("type") == "function"
    }
    assert "send_email" not in tool_names, (
        "send_email must never be exposed as an agent tool — "
        "email sending requires human approval via POST /invites/{id}/approve"
    )


def test_agent_tools_are_only_expected_names() -> None:
    """Whitelist check: agent has exactly the 5 expected read-only/scope-limited tools."""
    tool_names = {
        schema["function"]["name"]
        for schema in _TOOL_SCHEMAS
        if schema.get("type") == "function"
    }
    expected = {
        "get_candidate_profile",
        "parse_missing_fields",
        "rescore_candidate",
        "request_more_candidates",
        "finalize_shortlist",
    }
    assert tool_names == expected, (
        f"Unexpected agent tools. Got: {tool_names}. Expected: {expected}"
    )
