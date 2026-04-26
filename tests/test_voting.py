from __future__ import annotations

import pytest

from auto_film_conductor.domain import PollCandidate
from auto_film_conductor.voting import MockVotingProvider


@pytest.mark.asyncio
async def test_approval_votes_rank_by_total_then_candidate_id() -> None:
    provider = MockVotingProvider()
    poll_id = await provider.create_poll(
        kind="approval",
        title="Approval",
        candidates=[
            PollCandidate(id="b", title="B"),
            PollCandidate(id="a", title="A"),
            PollCandidate(id="c", title="C"),
        ],
        duration_seconds=300,
    )

    await provider.cast_approval_vote(poll_id, "u1", ["b", "a"])
    await provider.cast_approval_vote(poll_id, "u2", ["a"])
    await provider.cast_approval_vote(poll_id, "u3", ["b"])

    result = await provider.get_results(poll_id)

    assert result.scores == {"b": 2, "a": 2, "c": 0}
    assert result.ranked_candidate_ids == ["a", "b", "c"]
    assert result.winner_candidate_id == "a"


@pytest.mark.asyncio
async def test_rcv_eliminates_lowest_and_transfers_votes() -> None:
    provider = MockVotingProvider()
    poll_id = await provider.create_poll(
        kind="rcv",
        title="Runoff",
        candidates=[
            PollCandidate(id="a", title="A"),
            PollCandidate(id="b", title="B"),
            PollCandidate(id="c", title="C"),
        ],
        duration_seconds=300,
    )

    await provider.cast_rcv_vote(poll_id, "u1", ["a", "b", "c"])
    await provider.cast_rcv_vote(poll_id, "u2", ["b", "a", "c"])
    await provider.cast_rcv_vote(poll_id, "u3", ["c", "b", "a"])

    result = await provider.get_results(poll_id)

    assert result.winner_candidate_id == "b"
    assert result.ranked_candidate_ids[0] == "b"
