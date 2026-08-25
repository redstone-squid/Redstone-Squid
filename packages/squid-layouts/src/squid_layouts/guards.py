"""Per-action admission: may *this* press execute right now?

Access policies (`sd.access`) answer who may interact with a message; guards answer
whether one control may run this instant. They compose, and neither subsumes the other: a
cooldown, a deadline, or a single privileged button on an otherwise public panel is a guard.

Guards never affect rendering. A denial is a private notice, because the framework cannot
re-render a panel when a cooldown happens to expire; `available=` remains the render-time
tool and the two are routinely used together.

Admission has a third answer besides yes and no: *not yet — ask the actor*. A guard says
that by returning a `Challenge`, and the press is dropped rather than parked; approving it
starts a fresh one. See `docs/plans/squid-layouts-redesign/64-challenged-admission.md`.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Protocol, cast

from squid_layouts.interactions import ActionEvent
from squid_layouts.text import TextLike

if TYPE_CHECKING:
    from squid_layouts.patterns.decision import DecisionState
    from squid_layouts.patterns.shells import PatternEvent
    from squid_layouts.runtime.component import Component


def _monotonic() -> float:
    return time.monotonic()


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """One guard's answer, and the wording a denial is entitled to."""

    allowed: bool
    reason: TextLike | None = None
    """Author wording for a denial; `None` falls back to chrome."""
    retry_after: float | None = None
    """Seconds until the same press would be admitted, when the guard can say."""


ADMIT = GuardVerdict(allowed=True)
"""The shared admission verdict; guards return this rather than building one."""


def deny(reason: TextLike | None = None, *, retry_after: float | None = None) -> GuardVerdict:
    """Refuse a press, optionally with wording and a delay.

    `reason` wins over `retry_after` when both are given: explicit author wording beats
    generated wording, and the delay is then advisory metadata for a host reading verdicts.
    """
    return GuardVerdict(False, reason, retry_after)  # noqa: FBT003


class ChallengeResolver(Protocol):
    """The two answers a presented challenge can be given, from inside its own dialog.

    Both are one-shot and neither blocks: `approve` hands the press to something that will
    run it outside the dialog's own dispatch, and returns.
    """

    async def approve(self) -> None: ...

    async def decline(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Challenge:
    """Admission deferred to the actor. Approval re-enters the same press from the top.

    The core names *what to ask*; the frontend owns how it is shown and where the answer
    runs. A mount with no challenge presenter configured treats a challenge as a programmer
    error rather than silently admitting.
    """

    ask: Callable[[ChallengeResolver], Component]
    """Builds the dialog. Called once per challenge, with the resolver its controls answer through."""
    deadline: float | None = 120.0
    """Seconds the dialog stays answerable; `None` leaves it up for the mount's lifetime."""
    on_decline: TextLike | None = None
    """Private wording shown when the actor declines; `None` says nothing, because a dialog
    that closes is already the answer."""


type GuardOutcome = GuardVerdict | Challenge
"""What one admission pass may answer: yes, no, or a question."""


class GuardScope(StrEnum):
    """Whose behaviour a stateful guard counts."""

    ACTOR = "actor"
    MOUNT = "mount"


@dataclass(slots=True)
class _Staged:
    """One admission pass's ledger writes, held back until its outcome is known.

    A pass that ends in a challenge must record nothing: `confirm` belongs last in a chain,
    so it is exactly the guard whose non-admit outcome would otherwise spend every earlier
    one, and the actor who cancelled would be the one paying the cooldown. Buffering sits
    here rather than at the verdict because a guard may write and then deny in the same call.

    Denial is not buffered away — `all_of(cooldown(5), permission(deny))` spends the cooldown
    today, deliberately, and only a challenge rolls a pass back.
    """

    writes: dict[str, Any] = field(default_factory=dict)
    cleared: set[str] = field(default_factory=set)
    cleared_all: bool = False


@dataclass(frozen=True, slots=True)
class GuardLedger:
    """Mount-owned state for stateful guards, seen by a guard as one action-scoped view.

    Guard objects are rebuilt by the factories on every render, so they cannot hold state.
    The mount owns one ledger for its whole lifetime and hands each dispatch a view bound to
    the action key being pressed; `bucket` defaults to that key, so two actions carrying
    `cooldown(5)` get two buckets, while `cooldown(5, key="votes")` on both gets one.
    """

    action: str = ""
    now: Callable[[], float] = _monotonic
    _entries: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _staged: _Staged | None = field(default=None, repr=False, compare=False)

    def for_action(self, action: str) -> GuardLedger:
        """A view of this ledger scoped to one action key; the entries are shared."""
        return GuardLedger(action, self.now, self._entries, self._staged)

    def staged(self) -> GuardLedger:
        """A view whose writes are held until `commit`, for one admission pass.

        The frontend stages every pass and commits the ones that did not end in a challenge.
        Guards see no difference: they read their own writes back within the pass.
        """
        return GuardLedger(self.action, self.now, self._entries, _Staged())

    def commit(self) -> None:
        """Apply what a staged pass wrote. A no-op on an unstaged view.

        The view is spent once this returns; the frontend drops it and stages the next pass.
        """
        staged = self._staged
        if staged is None:
            return
        if staged.cleared_all:
            self._entries.clear()
        for key in staged.cleared:
            self._entries.pop(key, None)
        self._entries.update(staged.writes)

    def bucket(self, kind: str, *, per: GuardScope = GuardScope.ACTOR, actor: str = "", key: str | None = None) -> str:
        """The entry name for one guard kind, scoped as `per` says."""
        return f"{key or self.action}|{kind}|{actor if per is GuardScope.ACTOR else '*'}"

    def read[ValueT](self, key: str, default: ValueT) -> ValueT:
        """The entry stored under `key`, or `default` when nothing is stored."""
        if (staged := self._staged) is not None:
            if key in staged.writes:
                stored = staged.writes[key]
                return default if stored is None else cast(ValueT, stored)
            if staged.cleared_all or key in staged.cleared:
                return default
        stored = self._entries.get(key)
        return default if stored is None else cast(ValueT, stored)

    def write(self, key: str, value: object) -> None:
        if (staged := self._staged) is not None:
            staged.writes[key] = value
            staged.cleared.discard(key)
            return
        self._entries[key] = value

    def clear(self, key: str | None = None) -> None:
        """Forget one entry, or every entry this ledger holds."""
        if (staged := self._staged) is not None:
            if key is None:
                staged.writes.clear()
                staged.cleared.clear()
                staged.cleared_all = True
                return
            staged.writes.pop(key, None)
            staged.cleared.add(key)
            return
        if key is None:
            self._entries.clear()
            return
        self._entries.pop(key, None)


def approvals(ledger: GuardLedger, actor: str, *, key: str | None = None) -> str:
    """Where this action's outstanding approvals for one actor are counted.

    The contract between a challenging guard and the frontend: the frontend increments this
    when the actor approves, and each challenging guard in the resumed pass consumes one.
    A counter rather than a flag, so a chain holding two challenging guards converges — the
    pass that ends in the second challenge is discarded, so the first guard's approval is
    still there when the second one is answered.
    """
    return ledger.bucket("challenge", per=GuardScope.ACTOR, actor=actor, key=key)


class Guard(Protocol):
    """Decide whether one press may execute, given the mount's guard ledger."""

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardOutcome: ...


@dataclass(frozen=True, slots=True)
class _Cooldown:
    seconds: float
    per: GuardScope
    key: str | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        bucket = ledger.bucket("cooldown", per=self.per, actor=event.actor.id, key=self.key)
        now = ledger.now()
        last: float | None = ledger.read(bucket, None)
        if last is not None and (remaining := last + self.seconds - now) > 0:
            return deny(retry_after=remaining)
        ledger.write(bucket, now)
        return ADMIT


@dataclass(frozen=True, slots=True)
class _When:
    predicate: Callable[[ActionEvent], bool | Awaitable[bool]]
    reason: TextLike

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        del ledger
        outcome = self.predicate(event)
        allowed = await outcome if isawaitable(outcome) else outcome
        return ADMIT if allowed else deny(self.reason)


@dataclass(frozen=True, slots=True)
class _Permission:
    check: Callable[[ActionEvent], Awaitable[bool]]
    reason: TextLike | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        del ledger
        return ADMIT if await self.check(event) else deny(self.reason)


@dataclass(frozen=True, slots=True)
class _Once:
    per: GuardScope
    key: str | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        bucket = ledger.bucket("once", per=self.per, actor=event.actor.id, key=self.key)
        if ledger.read(bucket, default=False):
            return deny()
        ledger.write(bucket, value=True)
        return ADMIT


@dataclass(frozen=True, slots=True)
class _RateLimit:
    count: int
    per_seconds: float
    per: GuardScope
    key: str | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        bucket = ledger.bucket("rate_limit", per=self.per, actor=event.actor.id, key=self.key)
        now = ledger.now()
        recent = tuple(stamp for stamp in ledger.read(bucket, ()) if stamp > now - self.per_seconds)
        if len(recent) >= self.count:
            ledger.write(bucket, recent)
            return deny(retry_after=min(recent) + self.per_seconds - now)
        ledger.write(bucket, (*recent, now))
        return ADMIT


@dataclass(frozen=True, slots=True)
class _Until:
    deadline: datetime
    reason: TextLike | None

    def __post_init__(self) -> None:
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            message = "until() needs an aware deadline; a naive one has no instant to compare against"
            raise ValueError(message)

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        del event, ledger
        # Wall clock, unlike the elapsed-interval guards: a deadline is a fact about the
        # world, not about how long this process has been running.
        return ADMIT if datetime.now(UTC) < self.deadline else deny(self.reason)


@dataclass(frozen=True, slots=True)
class _AllOf:
    guards: tuple[Guard, ...]

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardOutcome:
        for guard in self.guards:
            outcome = await guard.admit(event, ledger)
            # Denial and challenge both stop the chain: it never asks a question it is about
            # to deny anyway.
            if isinstance(outcome, Challenge) or not outcome.allowed:
                return outcome
        return ADMIT


@dataclass(frozen=True, slots=True)
class _AnyOf:
    guards: tuple[Guard, ...]

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardOutcome:
        last = deny()
        for guard in self.guards:
            outcome = await guard.admit(event, ledger)
            # Unlike `all_of`, this one walks past denials -- that is what makes it `any_of`.
            # A challenge is not a "no", so it is returned rather than counted as one.
            if isinstance(outcome, Challenge):
                return outcome
            if outcome.allowed:
                return outcome
            last = outcome
        return last


@dataclass(frozen=True, slots=True)
class _Confirm:
    prompt: TextLike
    danger: bool
    deadline: float | None
    on_decline: TextLike | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardOutcome:
        bucket = approvals(ledger, event.actor.id)
        outstanding: int = ledger.read(bucket, 0)
        if outstanding > 0:
            ledger.write(bucket, outstanding - 1)
            return ADMIT
        return Challenge(self._ask, deadline=self.deadline, on_decline=self.on_decline)

    def _ask(self, resolver: ChallengeResolver) -> Component:
        # Imported here because `patterns` is built on `semantic`, which is built on this
        # module: the rendering half of a confirmation may depend on the vocabulary, but the
        # vocabulary cannot depend on it at import time.
        from squid_layouts.patterns.decision import confirm as confirm_shell
        from squid_layouts.semantic import Tone

        async def approved(event: PatternEvent[DecisionState]) -> None:
            # Closing first answers the click inside its own deadline, and leaves nothing in
            # this handler that could fail after the press has been handed on.
            await event.source.finish()
            await resolver.approve()

        async def declined(event: PatternEvent[DecisionState]) -> None:
            await event.source.finish()
            await resolver.decline()

        return confirm_shell(
            self.prompt,
            on_confirm=approved,
            on_cancel=declined,
            tone=Tone.DANGER if self.danger else Tone.NEUTRAL,
        )


def cooldown(seconds: float, *, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard:
    """Admit at most one press every `seconds`, counted per actor unless `per` says otherwise.

    The clock starts at admission, not at commit: a handler that fails still spends the
    cooldown, so a failing action cannot become an unguarded retry loop.
    """
    return _Cooldown(seconds, per, key)


def when(predicate: Callable[[ActionEvent], bool | Awaitable[bool]], *, reason: TextLike) -> Guard:
    """Admit while `predicate` holds; it may be synchronous or awaitable."""
    return _When(predicate, reason)


def permission(check: Callable[[ActionEvent], Awaitable[bool]], *, reason: TextLike | None = None) -> Guard:
    """Admit while an asynchronous authorization check passes."""
    return _Permission(check, reason)


def once(*, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard:
    """Admit one press for the life of the mount, per actor unless `per` says otherwise."""
    return _Once(per, key)


def rate_limit(
    count: int,
    per_seconds: float,
    *,
    per: GuardScope = GuardScope.ACTOR,
    key: str | None = None,
) -> Guard:
    """Admit `count` presses per rolling `per_seconds` window."""
    if count < 1:
        message = "rate_limit() needs a count of at least 1"
        raise ValueError(message)
    return _RateLimit(count, per_seconds, per, key)


def until(deadline: datetime, *, reason: TextLike | None = None) -> Guard:
    """Admit until an aware `deadline` passes, and never again."""
    return _Until(deadline, reason)


def all_of(*guards: Guard) -> Guard:
    """Admit only when every guard admits; the first denial is reported.

    Order matters for stateful guards: an earlier `cooldown` records its press before a
    later `permission` gets to deny it, so put the cheap unconditional checks first. That
    advice cannot work for `confirm`, which belongs last -- so a pass ending in a challenge
    records nothing at all, and the guards run again on approval.
    """
    return _AllOf(guards)


def any_of(*guards: Guard) -> Guard:
    """Admit when any guard admits; the last denial is reported.

    Stateful guards ahead of the admitting one still record their press, for the same
    reason `all_of` cares about order. A challenge from any member is returned as one --
    a question is not a "no" -- which gives this composite an ordering rule opposite to
    `all_of`'s: `any_of(confirm(...), permission(admin))` asks an admin the permission
    branch would have admitted for free, so `confirm` belongs last here too.
    """
    return _AnyOf(guards)


def confirm(
    prompt: TextLike,
    *,
    danger: bool = True,
    deadline: float | None = 120.0,
    on_decline: TextLike | None = None,
) -> Guard:
    """Admit once the actor reaffirms this press, and ask them when they have not.

    The two-press "are you sure" state machine, declared where the control is rather than
    hand-rolled in component state: no armed flag, no early return, no relabelling. The
    first press opens a private confirmation and executes nothing; approving it re-runs the
    whole funnel, so access lost or a cooldown started while the dialog was open still
    refuse the press the actor confirmed.

    Put it last in an `all_of`: a chain should not ask a question it is about to deny, and
    an earlier guard's record is discarded by the pass that ends in the question.
    """
    return _Confirm(prompt, danger, deadline, on_decline)


__all__ = [
    "ADMIT",
    "Challenge",
    "ChallengeResolver",
    "Guard",
    "GuardLedger",
    "GuardOutcome",
    "GuardScope",
    "GuardVerdict",
    "all_of",
    "any_of",
    "approvals",
    "confirm",
    "cooldown",
    "deny",
    "once",
    "permission",
    "rate_limit",
    "until",
    "when",
]
