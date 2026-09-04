"""What a message root stages before it commits: candidates, dispatch bookkeeping, busy paint.

A candidate is one staged render generation; it becomes the mount's state only when
committed, and every drawn candidate owes exactly one commit or rollback. The dispatch
profile and busy paint carry one action's operation-local facts through the same cycle.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Any

import discord

from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.document import Asset
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding, FormSpec
from squid_ui.guards import Challenge
from squid_ui.interactions import ActionBinding, BusySpec
from squid_ui.palette import Palette
from squid_ui.planning.navigation import NavFactory
from squid_ui.planning.target import AnyTarget
from squid_ui.profiling import (
    ActionStatus,
    DetachedSpanRecorder,
    DispatchDisposition,
    DispatchResult,
    GenerationDecision,
    OperationRecorder,
    PresentationStatus,
    TraceResult,
    TraceStatus,
)
from squid_ui.runtime.component import ComponentTree
from squid_ui.runtime.presentation_state import SessionUpdate
from squid_ui.scene.model import PlanResult
from squid_ui.target_types import DiscordTarget
from squid_ui.text import Localization, TextLike
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root_wiring import AnyMountedView
from squid_ui_discord.rendering import RenderedMessage

if TYPE_CHECKING:
    from squid_ui_discord.message_root import AnyMessageRoot


@dataclass(frozen=True, slots=True)
class _SubmitBinding(ActionBinding):
    """A form submission's binding, carrying the schema its values must be parsed against."""

    spec: FormSpec | None = None


@dataclass(frozen=True, slots=True)
class _RenewalBinding(ActionBinding):
    """Framework lifecycle action kept out of application dispatch policy."""


@dataclass(slots=True)
class _Candidate[RenderTargetT: DiscordTarget]:
    """One staged render generation, which becomes the mount's state only when committed."""

    view: AnyMountedView
    rendered: RenderedMessage[Any]
    tree: ComponentTree[RenderTargetT]
    handlers: dict[str, ActionBinding]
    form_bindings: Mapping[str, FormBinding]
    generation: int
    revision: int
    assets: tuple[Asset, ...]
    # Presentation writes this render earned; a failed delivery simply drops them.
    session_updates: tuple[SessionUpdate, ...]
    settled: bool = False
    """Whether this candidate has already been committed or rolled back.

    Every path that draws a candidate owes it exactly one of those two endings. Which
    candidate is outstanding is already unambiguous -- `_draw` stages subscriptions, and
    the reconciler refuses a second staged set -- so what is left to get wrong is settling
    the same one twice, and that is what this refuses.
    """

    @property
    def payload(self) -> MessagePayload:
        """The complete message this render delivers to.

        The render already built it, in whichever mode the target chose. A message root does
        not reassemble one, because doing so would be a second place that has to know what
        each mode is allowed to carry.
        """
        return self.rendered.payload

    @property
    def plan(self) -> PlanResult:
        return self.rendered.plan


@dataclass(slots=True)
class _PlannedCandidate[RenderTargetT: DiscordTarget]:
    """A staged application render whose visible identity was checked before drawing."""

    plan: PlanResult
    tree: ComponentTree[RenderTargetT]
    handlers: dict[str, ActionBinding]
    form_bindings: Mapping[str, FormBinding]
    revision: int
    assets: tuple[Asset, ...]
    session_updates: tuple[SessionUpdate, ...]
    settled: bool = False


type _ApplicationCandidate[RenderTargetT: DiscordTarget] = _Candidate[RenderTargetT] | _PlannedCandidate[RenderTargetT]


@dataclass(frozen=True, slots=True)
class _PlanEnvironment:
    """Every mutable owner input not carried by a component tree."""

    target: AnyTarget
    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    nav: NavFactory
    status: TextLike | None
    presentation_revision: int


def _drawn[RenderTargetT: DiscordTarget](
    candidate: _ApplicationCandidate[RenderTargetT],
) -> _Candidate[RenderTargetT]:
    if not isinstance(candidate, _Candidate):
        message = "an undrawn preflight candidate did not match the live scene"
        raise LayoutInvariantError(message)
    return candidate


def _scene_action_keys(document: scene.Scene) -> tuple[str, ...]:
    """Collect visible action references without allocating frontend controls."""
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, scene.Button | scene.Select | scene.EntitySelect):
            found.append(value.action)
            return
        if is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                walk(getattr(value, item.name))
            return
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item)
            return
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for item in value:
                walk(item)

    walk(document.body)
    return tuple(found)


@dataclass(slots=True)
class _LifecycleCandidate:
    """A framework-owned visible generation that commits no component runtime state."""

    view: AnyMountedView
    rendered: RenderedMessage[Any]
    handlers: dict[str, ActionBinding]
    generation: int

    @property
    def payload(self) -> MessagePayload:
        return self.rendered.payload


@dataclass(slots=True)
class _DispatchProfile:
    """Mutable operation-local facts frozen into a dispatch result at the terminal branch."""

    operation: OperationRecorder
    interaction: discord.Interaction
    generation: GenerationDecision
    acknowledgement: DetachedSpanRecorder
    action: ActionStatus = ActionStatus.NOT_RUN
    presentation: PresentationStatus = PresentationStatus.NOT_REQUIRED
    acknowledged: bool = False
    finished: bool = False
    resumed: bool = False
    """Whether an approved challenge started this press, which decides what may be edited.

    A resumed press carries the interaction that asked the question, not one that came from
    this mount's message, so nothing in the dispatch may take a handle from it: not the
    flush's edit target, not `_renew`'s standing-handle trade-up, and not the address a
    mount without one would otherwise learn. The mount redraws where it already lives.
    """

    def decide_generation(self, active: int, *, rebased: bool = False) -> None:
        self.generation = GenerationDecision(self.generation.submitted, active, rebased)

    def acknowledge(self, source: str) -> None:
        if not self.acknowledged and self.interaction.response.is_done():
            self.acknowledged = True
            self.acknowledgement.set_attribute("source", source)
            self.acknowledgement.finish()

    def finish(self, disposition: DispatchDisposition, error: Exception | None = None) -> None:
        """End this dispatch profile with its terminal disposition."""
        if self.finished:
            return
        self.finished = True
        self.operation.increment("dispatch.rebased", int(self.generation.rebased))
        self.acknowledge("action")
        # Both challenge dispositions fall through to COMPLETED, which is right: asking a
        # question and being told no are results, not failures.
        status = (
            TraceStatus.CANCELLED
            if disposition is DispatchDisposition.CANCELLED
            else TraceStatus.FAILED
            if disposition
            in {
                DispatchDisposition.ACCESS_FAILED,
                DispatchDisposition.GUARD_FAILED,
                DispatchDisposition.ACTION_FAILED,
                DispatchDisposition.DELIVERY_FAILED,
            }
            else TraceStatus.COMPLETED
        )
        detail = None if error is None else f"{type(error).__module__}.{type(error).__qualname__}"
        self.operation.set_result(
            TraceResult(
                status,
                detail,
                DispatchResult(disposition, self.action, self.presentation, self.generation),
            )
        )


@dataclass(frozen=True, slots=True)
class _Admission:
    """What the admission stage decided, and the question it wants put to the actor.

    Three-valued because a challenge is not a refusal the funnel can simply return on: the
    dialog is presented after the action lock is released, so `_dispatch_binding` has to
    carry it back out rather than answer it in place.
    """

    admitted: bool
    challenge: Challenge | None = None


_ADMITTED = _Admission(admitted=True)


_REFUSED = _Admission(admitted=False)


"""Refused *and already answered*: the guard denied or raised, and the profile is finished."""


class _BusyPaint:
    """One action's interim "working" render, and the ordering between it and the flush.

    The paint is scheduled by the acknowledgement watchdog and the flush by the handler
    returning, so the two race. They are ordered by this object's lock rather than by
    cancellation: `close()` waits out a paint already in flight and then latches, so a
    watchdog that wakes late paints nothing over the final render.
    """

    def __init__(
        self,
        message_root: AnyMessageRoot,
        key: str,
        busy: BusySpec,
        interaction: discord.Interaction,
        *,
        resumed: bool = False,
    ) -> None:
        self._root = message_root
        self._key = key
        self._busy = busy
        self._interaction = interaction
        self._resumed = resumed
        self._lock = asyncio.Lock()
        self._closed = False
        self._shown = False

    @property
    def shown(self) -> bool:
        """Whether an interim render is currently what the reader is looking at."""
        return self._shown

    async def show(self, profile: _DispatchProfile) -> None:
        """Relabel the pressed control and disable the panel, once."""
        async with self._lock:
            if self._closed or self._shown or self._root._finished:
                return
            pending = self._busy.pending
            label = self._root._chrome_text(self._root.chrome.working if pending is None else pending)
            source = self._root._source(self._interaction, resumed=self._resumed)
            wrote = await self._root._repaint(self._key, label, through=source)
            if wrote is None:
                # Nothing could write, so the click is still unanswered: the watchdog goes
                # on to defer it at the usual deadline.
                return
            self._shown = True
            if wrote is source:
                profile.acknowledge("busy")

    async def close(self) -> bool:
        """End this busy paint, preventing future writes and reporting if one is visible."""
        async with self._lock:
            self._closed = True
            return self._shown

    async def restore(self) -> None:
        """Put the committed scene back, live controls and all."""
        async with self._lock:
            if not self._shown or self._root._finished:
                return
            self._shown = False
            await self._root._repaint(None, None, through=self._root._source(self._interaction, resumed=self._resumed))
