"""Contract tests for validate_output pipeline node — all 8 checks."""
import uuid
import pytest
from app.pipeline.nodes.verify import validate_output
from app.pipeline.state import EnrichedCandidate, PipelineState, ShortlistEntry

TENANT_ID = str(uuid.uuid4())
_CIDS = [str(uuid.uuid4()) for _ in range(5)]

_ENRICHED: list[EnrichedCandidate] = [
    {
        "candidate_id": cid,
        "resume_text": f"Candidate {i} has 5 years Python experience at ACME.",
        "structured_jsonb": {},
        "rerank_score": 0.9 - i * 0.02,
        "enriched_fields": {},
    }
    for i, cid in enumerate(_CIDS)
]


def _valid_shortlist() -> list[ShortlistEntry]:
    return [
        ShortlistEntry(
            rank=i + 1,
            candidate_id=_CIDS[i],
            score=0.9 - i * 0.05,
            justification=f"Candidate {i} has 5 years Python experience at ACME.",
            citations=["5 years Python experience at ACME"],
        )
        for i in range(5)
    ]


def _base_state(shortlist: list[ShortlistEntry] | None = None) -> PipelineState:
    return {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_ID,
        "job_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_description": "Python engineer",
        "enriched_candidates": _ENRICHED,
        "shortlist": shortlist if shortlist is not None else _valid_shortlist(),
        "loop_count": 0,
        "total_cost_usd": 0.5,
        "agent_trace": [],
    }


@pytest.mark.asyncio
async def test_valid_shortlist_passes_all_checks() -> None:
    result = await validate_output(_base_state())
    assert result.get("validation_errors") == []
    assert result["loop_count"] == 0


@pytest.mark.asyncio
async def test_check_1_wrong_count() -> None:
    state = _base_state(shortlist=_valid_shortlist()[:3])
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("5 candidates" in e for e in errors)


@pytest.mark.asyncio
async def test_check_2_duplicate_candidates() -> None:
    sl = _valid_shortlist()
    sl[1] = ShortlistEntry(**{**sl[0], "rank": 2})
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("duplicate" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_check_3_candidate_not_in_pool() -> None:
    sl = _valid_shortlist()
    sl[0] = ShortlistEntry(
        rank=1, candidate_id=str(uuid.uuid4()), score=0.9,
        justification="5 years Python experience at ACME.", citations=["5 years Python experience at ACME"]
    )
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("not in pool" in e for e in errors)


@pytest.mark.asyncio
async def test_check_4_empty_justification() -> None:
    sl = _valid_shortlist()
    sl[0] = ShortlistEntry(**{**sl[0], "justification": "   "})
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("justification" in e for e in errors)


@pytest.mark.asyncio
async def test_check_5_citation_faithfulness_fails() -> None:
    sl = _valid_shortlist()
    sl[0] = ShortlistEntry(**{**sl[0], "citations": ["INVENTED PHRASE NOT IN RESUME"]})
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("citation" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_check_5_citation_faithfulness_passes_with_substring() -> None:
    sl = _valid_shortlist()
    result = await validate_output(_base_state(shortlist=sl))
    errors = result.get("validation_errors", [])
    assert not any("citation" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_check_6_score_out_of_range() -> None:
    sl = _valid_shortlist()
    sl[0] = ShortlistEntry(**{**sl[0], "score": 1.5})
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("score" in e for e in errors)


@pytest.mark.asyncio
async def test_check_7_cost_over_cap() -> None:
    state = _base_state()
    state["total_cost_usd"] = 2.0
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("cost" in e for e in errors)


@pytest.mark.asyncio
async def test_check_8_pii_in_justification() -> None:
    sl = _valid_shortlist()
    sl[0] = ShortlistEntry(**{
        **sl[0],
        "justification": "Great candidate, email: alice@example.com",
        "citations": ["5 years Python experience at ACME"],
    })
    state = _base_state(shortlist=sl)
    result = await validate_output(state)
    errors = result.get("validation_errors", [])
    assert any("pii" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_loop_count_increments_on_failure() -> None:
    state = _base_state(shortlist=_valid_shortlist()[:3])
    result = await validate_output(state)
    assert result["loop_count"] == 1
