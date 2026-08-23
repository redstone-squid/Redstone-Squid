"""Per-action admission: may *this* press execute right now?

Access policies (`sl.discord.access`) answer who may interact with a message; guards answer
whether one control may run this instant. They compose, and neither subsumes the other: a
cooldown, a deadline, or a single privileged button on an otherwise public panel is a guard.

Guards never affect rendering. A denial is a private notice, because the framework cannot
re-render a panel when a cooldown happens to expire; `available=` remains the render-time
tool and the two are routinely used together.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from inspect import isawaitable
from typing import Any, Protocol, cast

from squid_layouts.interactions import ActionEvent
from squid_layouts.text import TextLike


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


class GuardScope(StrEnum):
    """Whose behaviour a stateful guard counts."""

    ACTOR = "actor"
    MOUNT = "mount"


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

    def for_action(self, action: str) -> GuardLedger:
        """A view of this ledger scoped to one action key; the entries are shared."""
        return GuardLedger(action, self.now, self._entries)

    def bucket(self, kind: str, *, per: GuardScope = GuardScope.ACTOR, actor: str = "", key: str | None = None) -> str:
        """The entry name for one guard kind, scoped as `per` says."""
        return f"{key or self.action}|{kind}|{actor if per is GuardScope.ACTOR else '*'}"

    def read[ValueT](self, key: str, default: ValueT) -> ValueT:
        """The entry stored under `key`, or `default` when nothing is stored."""
        stored = self._entries.get(key)
        return default if stored is None else cast(ValueT, stored)

    def write(self, key: str, value: object) -> None:
        self._entries[key] = value

    def clear(self, key: str | None = None) -> None:
        """Forget one entry, or every entry this ledger holds."""
        if key is None:
            self._entries.clear()
            return
        self._entries.pop(key, None)


class Guard(Protocol):
    """Decide whether one press may execute, given the mount's guard ledger."""

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict: ...


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

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        for guard in self.guards:
            verdict = await guard.admit(event, ledger)
            if not verdict.allowed:
                return verdict
        return ADMIT


@dataclass(frozen=True, slots=True)
class _AnyOf:
    guards: tuple[Guard, ...]

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict:
        last = deny()
        for guard in self.guards:
            verdict = await guard.admit(event, ledger)
            if verdict.allowed:
                return verdict
            last = verdict
        return last


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
    later `permission` gets to deny it, so put the cheap unconditional checks first.
    """
    return _AllOf(guards)


def any_of(*guards: Guard) -> Guard:
    """Admit when any guard admits; the last denial is reported.

    Stateful guards ahead of the admitting one still record their press, for the same
    reason `all_of` cares about order.
    """
    return _AnyOf(guards)
