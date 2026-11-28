"""Ballot-safe vote-session representations."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.builds.history import HistoryActor
from squid.core.voting.domain import (
    BuildEditVoteTarget,
    BuildVoteTarget,
    PollScope,
    VoteKind,
    VoteOption,
    VoteSelection,
    VoteSessionResult,
    VoteSessionSnapshot,
    VoteStatus,
    VoteVisibility,
)


class VoteOptionSummary(BaseModel):
    """A stable vote option without Discord-specific reaction aliases."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None
    choice: str
    multiplier: float
    """Scales the voter's weight, so one session can offer a small and a big approve."""
    position: int

    @classmethod
    def from_domain(cls, option: VoteOption) -> VoteOptionSummary:
        return cls(
            id=option.id,
            label=option.label,
            choice=option.choice.value,
            multiplier=option.multiplier,
            position=option.position,
        )


class VoteTallies(BaseModel):
    """Vote totals by option, with raw counts and weighted scores."""

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
    visibility: VoteVisibility
    scope: PollScope
    """Whether the poll accepts ballots from one server or from every server."""
    deadline: datetime
    voting_ended_at: datetime | None


class OwnVoteSelection(BaseModel):
    """The authenticated caller's own ballot selection."""

    model_config = ConfigDict(extra="forbid")

    option_id: str

    @classmethod
    def from_domain(cls, selection: VoteSelection) -> OwnVoteSelection:
        return cls(option_id=selection.option_id)


class VoteViewer(BaseModel):
    """Whether the caller may vote in the session, and the weight a ballot would get now."""

    model_config = ConfigDict(extra="forbid")

    eligible: bool
    weight: float | None
    """Weight before the chosen option's multiplier; null when not eligible."""


class VoteVoter(BaseModel):
    """A ballot on a poll that shows who voted."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None
    """Null if the voter has no public profile name or hides it."""
    option_id: str


class VoteSessionDetail(BaseModel):
    """Vote status, permitted totals, and the caller's own selection if present.

    Tallies are null while the session's visibility rules hide them. `voters` is null
    unless the session shows who voted, which only `visible_live` polls do.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: VoteKind
    status: VoteStatus
    result: VoteSessionResult
    pass_threshold: int | None
    fail_threshold: int | None
    submission_id: UUID | None = None
    build_id: int | None
    options: list[VoteOptionSummary]
    tallies: VoteTallies | None
    poll: VotePollSummary | None
    own_selection: OwnVoteSelection | None
    voters: list[VoteVoter] | None
    viewer: VoteViewer | None = None
    """Null for anonymous callers and sessions that no longer accept ballots."""

    @classmethod
    def from_domain(cls, session: VoteSessionSnapshot, *, caller_account_id: int | None = None) -> VoteSessionDetail:
        tallies = None
        if session.shows_tallies:
            tallies = VoteTallies(
                raw=dict(session.raw_tallies()),
                weighted=dict(session.weighted_tallies()),
                upvotes=session.upvotes,
                downvotes=session.downvotes,
                net=session.net_votes,
            )
        poll = session.poll
        own_selection = next(
            (selection for selection in session.selections if selection.voter.account_id == caller_account_id), None
        )
        return cls(
            id=session.id,
            kind=session.kind,
            status=session.status,
            result=session.result,
            pass_threshold=session.pass_threshold,
            fail_threshold=session.fail_threshold,
            submission_id=session.target.submission_id
            if isinstance(session.target, BuildVoteTarget | BuildEditVoteTarget)
            else None,
            build_id=session.target.build_id
            if isinstance(session.target, BuildVoteTarget | BuildEditVoteTarget)
            else None,
            options=[VoteOptionSummary.from_domain(option) for option in session.menu.options],
            tallies=tallies,
            poll=(
                None
                if poll is None
                else VotePollSummary(
                    question=poll.question,
                    visibility=poll.visibility,
                    scope=poll.scope,
                    deadline=poll.deadline.to_stdlib(),
                    voting_ended_at=None if poll.voting_ended_at is None else poll.voting_ended_at.to_stdlib(),
                )
            ),
            own_selection=OwnVoteSelection.from_domain(own_selection) if own_selection is not None else None,
            voters=None
            if session.is_anonymous
            else [
                VoteVoter(display_name=selection.display_name, option_id=selection.option_id)
                for selection in session.selections
            ],
        )


class ReviewVoteSummary(VoteSessionDetail):
    """A build review vote with the build it decides on.

    `build` is null if the build has been deleted since the vote opened.
    """

    build: PublicBuildSummary | None
    submitted_by: HistoryActor | None
