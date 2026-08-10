"""Ballot-safe vote-session representations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from squid.voting.domain import VoteOption, VoteSelection, VoteSessionSnapshot


class VoteOptionSummary(BaseModel):
    """A stable vote option without Discord-specific reaction aliases."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None
    choice: str
    position: int

    @classmethod
    def from_domain(cls, option: VoteOption) -> "VoteOptionSummary":
        assert option.identifier is not None
        return cls(id=option.identifier, label=option.label, choice=option.choice.value, position=option.position)


class VoteTallies(BaseModel):
    """Aggregate results which cannot identify individual voters."""

    model_config = ConfigDict(extra="forbid")

    raw: dict[str, int]
    weighted: dict[str, float]
    upvotes: float
    downvotes: float
    net: float


class VotePollSummary(BaseModel):
    """Public generic-poll metadata."""

    model_config = ConfigDict(extra="forbid")

    question: str
    visibility: str
    deadline: datetime


class OwnVoteSelection(BaseModel):
    """The authenticated caller's own ballot selection."""

    model_config = ConfigDict(extra="forbid")

    option_id: str

    @classmethod
    def from_domain(cls, selection: VoteSelection) -> "OwnVoteSelection":
        return cls(option_id=selection.option_id)


class VoteSessionDetail(BaseModel):
    """A vote session with aggregate-only ballot data."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    status: str
    result: str
    pass_threshold: int
    fail_threshold: int
    build_id: int | None
    options: list[VoteOptionSummary]
    tallies: VoteTallies | None
    poll: VotePollSummary | None
    own_selection: OwnVoteSelection | None

    @classmethod
    def from_domain(cls, session: VoteSessionSnapshot, *, caller_account_id: int | None = None) -> "VoteSessionDetail":
        options_by_id: dict[str, VoteOption] = {}
        for option in session.options:
            assert option.identifier is not None
            options_by_id.setdefault(option.identifier, option)
        hide_tallies = (
            session.poll is not None and session.poll.visibility == "anonymous_hidden" and session.status == "open"
        )
        tallies = None
        if not hide_tallies:
            tallies = VoteTallies(
                raw=dict(session.raw_tallies()),
                weighted=dict(session.weighted_tallies()),
                upvotes=session.upvotes,
                downvotes=session.downvotes,
                net=session.net_votes,
            )
        poll = session.poll
        own_selection = next(
            (selection for selection in session.selections if selection.account_id == caller_account_id), None
        )
        return cls(
            id=session.id,
            kind=session.kind,
            status=session.status,
            result=session.result,
            pass_threshold=session.pass_threshold,
            fail_threshold=session.fail_threshold,
            build_id=session.target.build_id,
            options=[VoteOptionSummary.from_domain(option) for option in options_by_id.values()],
            tallies=tallies,
            poll=(
                None
                if poll is None
                else VotePollSummary(
                    question=poll.question,
                    visibility=poll.visibility,
                    deadline=poll.deadline.to_stdlib(),
                )
            ),
            own_selection=OwnVoteSelection.from_domain(own_selection) if own_selection is not None else None,
        )
