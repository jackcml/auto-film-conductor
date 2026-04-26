from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from uuid import uuid4

from auto_film_conductor.domain import PollCandidate, PollResult


@dataclass
class MockPoll:
    id: str
    kind: str
    title: str
    candidates: list[PollCandidate]
    duration_seconds: int
    is_open: bool = True
    approval_votes: dict[str, set[str]] = field(default_factory=dict)
    rcv_votes: dict[str, list[str]] = field(default_factory=dict)


class MockVotingProvider:
    def __init__(self) -> None:
        self._polls: dict[str, MockPoll] = {}

    async def create_poll(
        self,
        *,
        kind: str,
        title: str,
        candidates: list[PollCandidate],
        duration_seconds: int,
    ) -> str:
        if kind not in {"approval", "rcv"}:
            raise ValueError(f"Unsupported poll kind: {kind}")
        poll_id = str(uuid4())
        self._polls[poll_id] = MockPoll(
            id=poll_id,
            kind=kind,
            title=title,
            candidates=candidates,
            duration_seconds=duration_seconds,
        )
        return poll_id

    async def close_poll(self, poll_id: str) -> None:
        self._poll(poll_id).is_open = False

    async def get_results(self, poll_id: str) -> PollResult:
        poll = self._poll(poll_id)
        if poll.kind == "approval":
            scores = {candidate.id: len(poll.approval_votes.get(candidate.id, set())) for candidate in poll.candidates}
            ranked = _rank_by_score(scores)
            return PollResult(poll_id=poll_id, winner_candidate_id=ranked[0] if ranked else None, ranked_candidate_ids=ranked, scores=scores)

        return _rcv_result(poll)

    async def cast_approval_vote(self, poll_id: str, voter_id: str, candidate_ids: list[str]) -> None:
        poll = self._poll(poll_id)
        self._ensure_open(poll)
        self._ensure_kind(poll, "approval")
        valid_ids = {candidate.id for candidate in poll.candidates}
        for voters in poll.approval_votes.values():
            voters.discard(voter_id)
        for candidate_id in candidate_ids:
            if candidate_id in valid_ids:
                poll.approval_votes.setdefault(candidate_id, set()).add(voter_id)

    async def cast_rcv_vote(self, poll_id: str, voter_id: str, ranking: list[str]) -> None:
        poll = self._poll(poll_id)
        self._ensure_open(poll)
        self._ensure_kind(poll, "rcv")
        valid_ids = {candidate.id for candidate in poll.candidates}
        deduped = []
        for candidate_id in ranking:
            if candidate_id in valid_ids and candidate_id not in deduped:
                deduped.append(candidate_id)
        poll.rcv_votes[voter_id] = deduped

    def snapshot(self, poll_id: str) -> MockPoll:
        return self._poll(poll_id)

    def _poll(self, poll_id: str) -> MockPoll:
        try:
            return self._polls[poll_id]
        except KeyError as exc:
            raise KeyError(f"Unknown poll: {poll_id}") from exc

    @staticmethod
    def _ensure_open(poll: MockPoll) -> None:
        if not poll.is_open:
            raise ValueError("Poll is closed")

    @staticmethod
    def _ensure_kind(poll: MockPoll, kind: str) -> None:
        if poll.kind != kind:
            raise ValueError(f"Poll {poll.id} is {poll.kind}, not {kind}")


def _rank_by_score(scores: dict[str, int]) -> list[str]:
    return [candidate_id for candidate_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _rcv_result(poll: MockPoll) -> PollResult:
    active = {candidate.id for candidate in poll.candidates}
    round_scores: dict[str, int] = {candidate.id: 0 for candidate in poll.candidates}

    while active:
        counts = Counter[str]()
        exhausted = 0
        for ranking in poll.rcv_votes.values():
            vote = next((candidate_id for candidate_id in ranking if candidate_id in active), None)
            if vote is None:
                exhausted += 1
            else:
                counts[vote] += 1

        for candidate_id in active:
            round_scores[candidate_id] = counts[candidate_id]

        total = sum(counts.values())
        if total == 0:
            ranked = sorted(active)
            return PollResult(poll_id=poll.id, winner_candidate_id=ranked[0] if ranked else None, ranked_candidate_ids=ranked, scores=round_scores)

        leader, leader_votes = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
        if leader_votes > total / 2 or len(active) == 1:
            ranked = [leader] + [candidate_id for candidate_id in _rank_by_score(round_scores) if candidate_id != leader]
            return PollResult(poll_id=poll.id, winner_candidate_id=leader, ranked_candidate_ids=ranked, scores=round_scores)

        min_votes = min(counts.get(candidate_id, 0) for candidate_id in active)
        losers = sorted(candidate_id for candidate_id in active if counts.get(candidate_id, 0) == min_votes)
        active.remove(losers[0])

    return PollResult(poll_id=poll.id, winner_candidate_id=None, ranked_candidate_ids=[], scores=round_scores)
