"""Ballot-safe vote-session representations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from squid.voting.domain import (
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

    id: str = Field(description="Stable option identifier, and the key used in `tallies` and `own_selection`.")
    label: str | None = Field(
        description="Display text. Always set on a generic poll's options; null on the fixed approve and deny ones, "
        "which a client labels itself."
    )
    choice: str = Field(
        description="`approve` and `deny` sign the option's weight in the net score; `generic` options do not score."
    )
    position: int = Field(description="Display order, ascending.")

    @classmethod
    def from_domain(cls, option: VoteOption) -> VoteOptionSummary:
        assert option.identifier is not None
        return cls(id=option.identifier, label=option.label, choice=option.choice.value, position=option.position)


class VoteTallies(BaseModel):
    """Aggregate results which cannot identify individual voters."""

    model_config = ConfigDict(extra="forbid")

    raw: dict[str, int] = Field(description="How many ballots chose each option, by option id.")
    weighted: dict[str, float] = Field(description="Summed ballot weights per option id.")
    upvotes: float = Field(description="Total weight cast in favour.")
    downvotes: float = Field(description="Total weight cast against, as a positive number.")
    net: float = Field(description="`upvotes` minus `downvotes`, the score a threshold vote closes on.")


class VotePollSummary(BaseModel):
    """Public generic-poll metadata."""

    model_config = ConfigDict(extra="forbid")

    question: str
    visibility: VoteVisibility = Field(
        description="How much is disclosed while the poll is open: `anonymous_live` and `visible_live` publish "
        "tallies, `anonymous_hidden` withholds them until it closes."
    )
    scope: PollScope = Field(
        description="`guild` accepts ballots from one server, `network` from every server the bot serves."
    )
    deadline: datetime = Field(description="When the poll closes itself; a generic poll never closes on a score.")


class OwnVoteSelection(BaseModel):
    """The authenticated caller's own ballot selection."""

    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(description="The `id` of the option the caller chose.")

    @classmethod
    def from_domain(cls, selection: VoteSelection) -> OwnVoteSelection:
        return cls(option_id=selection.option_id)


class VoteSessionDetail(BaseModel):
    """A vote session with aggregate-only ballot data."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: VoteKind = Field(
        description="`build` and `delete_log` close themselves once `net` reaches a threshold; `generic` closes on "
        "its deadline."
    )
    status: VoteStatus = Field(description="`open` while ballots are accepted, `closed` afterwards.")
    result: VoteSessionResult = Field(
        description="`pending` until the session closes; then `approved`, `denied` or `cancelled`."
    )
    pass_threshold: int | None = Field(
        description="Positive net score that approves the session. Set on threshold kinds and null on a generic poll."
    )
    fail_threshold: int | None = Field(
        description="Negative net score that denies the session. Set on threshold kinds and null on a generic poll."
    )
    build_id: int | None = Field(description="Null unless the session targets a build.")
    options: list[VoteOptionSummary]
    tallies: VoteTallies | None = Field(
        description="Null while an `anonymous_hidden` poll is open; present once it closes."
    )
    poll: VotePollSummary | None = Field(description="Null unless `kind` is `generic`.")
    own_selection: OwnVoteSelection | None = Field(
        description="The authenticated caller's own ballot; null when they have not voted."
    )

    @classmethod
    def from_domain(cls, session: VoteSessionSnapshot, *, caller_account_id: int | None = None) -> VoteSessionDetail:
        options_by_id: dict[str, VoteOption] = {}
        for option in session.options:
            assert option.identifier is not None
            options_by_id.setdefault(option.identifier, option)
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
            (selection for selection in session.selections if selection.account_id == caller_account_id), None
        )
        return cls(
            id=session.id,
            kind=session.kind,
            status=session.status,
            result=session.result,
            pass_threshold=session.pass_threshold,
            fail_threshold=session.fail_threshold,
            build_id=session.target.build_id if isinstance(session.target, BuildVoteTarget) else None,
            options=[VoteOptionSummary.from_domain(option) for option in options_by_id.values()],
            tallies=tallies,
            poll=(
                None
                if poll is None
                else VotePollSummary(
                    question=poll.question,
                    visibility=poll.visibility,
                    scope=poll.scope,
                    deadline=poll.deadline.to_stdlib(),
                )
            ),
            own_selection=OwnVoteSelection.from_domain(own_selection) if own_selection is not None else None,
        )
