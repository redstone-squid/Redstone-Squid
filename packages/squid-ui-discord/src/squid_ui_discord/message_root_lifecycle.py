"""Own candidate settlement and the state published by a Discord message root.

Delivery stays with the root. Once it has a candidate, an attempt either publishes it
or discards its staged resources, including when delivery is cancelled. Application
commit settles before running component hooks: a hook failure cannot undo a delivered
tree. Renewal and terminal views never commit application component state.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from squid_ui.document import Asset
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding
from squid_ui.planning.result import PlanResult
from squid_ui.profiling import OperationRecorder
from squid_ui.runtime.component import ComponentTree
from squid_ui.runtime.owner import ComponentRuntime
from squid_ui.target_types import DiscordTarget
from squid_ui_discord.message_root_candidates import (
    _ApplicationCandidate,
    _Candidate,
    _LifecycleCandidate,
    _PlanEnvironment,
    _PlannedCandidate,
)
from squid_ui_discord.message_root_contracts import MessageRootStatus
from squid_ui_discord.message_root_wiring import AnyMountedView

logger = logging.getLogger(__name__)

type _SettlementCandidate[RenderTargetT: DiscordTarget] = _ApplicationCandidate[RenderTargetT] | _LifecycleCandidate


class _CandidateLifecycle[RenderTargetT: DiscordTarget]:
    """Coordinate staged resources, application commits, and visible generations.

    The root serializes attempts with its render lock. Callbacks supply the few owner
    inputs needed at commit time, without giving this owner delivery or policy duties.
    """

    def __init__(
        self,
        mount_id: str,
        runtime: ComponentRuntime[RenderTargetT],
        *,
        environment: Callable[[], _PlanEnvironment],
        remaining_timeout: Callable[[], float | None],
        committed: Callable[[], None],
        presented: Callable[[], None],
        track: Callable[[], None],
    ) -> None:
        self._mount_id = mount_id
        self._runtime = runtime
        self._environment = environment
        self._remaining_timeout = remaining_timeout
        self._committed = committed
        self._presented = presented
        self._track = track
        self.status = MessageRootStatus.ACTIVE
        self.view: AnyMountedView | None = None
        self.handlers: dict[str, ActionBinding] = {}
        # Forms and their opening actions share a key but have different bindings.
        self.form_bindings: Mapping[str, FormBinding] = {}
        self.generation = 0
        self.issued = 0
        self.pending: _Candidate[RenderTargetT] | None = None
        self.dirty = False
        self.suppressed = 0
        self.behind = False
        """Whether an action committed state that the message has not presented yet.

        Also set when delivering an application render fails, and cleared when any application
        render commits, including one suppressed because the message already matched it.
        While set, EXCLUSIVE presses repaint the message instead of running their handlers.
        """
        self.assets: tuple[Asset, ...] = ()
        self.plan: PlanResult | None = None
        self.planned_tree: ComponentTree[RenderTargetT, Any] | None = None
        self.planned_environment: _PlanEnvironment | None = None

    def issue_generation(self) -> int:
        """Reserve control ids that will never be reused, even if delivery fails."""
        self.issued += 1
        return self.issued

    def mark_dirty(self) -> None:
        self.dirty = True

    def stage_only(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Retain a synchronous test render until it is committed or superseded."""
        self.pending = candidate

    def supersede(self) -> None:
        if self.pending is not None:
            self.discard(self.pending)

    @contextmanager
    def attempt(self, candidate: _SettlementCandidate[RenderTargetT]) -> Iterator[None]:
        """Discard an unsettled candidate on every exit, including cancellation."""
        try:
            yield
        finally:
            if not candidate.settled:
                self.discard(candidate)

    def _settle(self, candidate: _SettlementCandidate[RenderTargetT], ending: str) -> None:
        if candidate.settled:
            generation = "undrawn" if isinstance(candidate, _PlannedCandidate) else candidate.generation
            message = f"mount {self._mount_id}: candidate generation {generation} already settled, cannot {ending}"
            raise LayoutInvariantError(message)
        if isinstance(candidate, _LifecycleCandidate):
            candidate.settled = True
        if self.pending is candidate:
            self.pending = None

    def _commit_application(self, candidate: _ApplicationCandidate[RenderTargetT]) -> None:
        self._settle(candidate, "commit")
        self.behind = False
        presentation = self._runtime.presentation
        presentation.apply_updates(candidate.session_updates)
        self._runtime.plan_memo.promote(presentation, presentation.revision)
        # Component hooks can invalidate or raise after reconciling the delivered tree.
        # Settlement precedes them so cleanup never rolls back that committed work.
        candidate.render.commit()
        self.handlers = candidate.handlers
        self.form_bindings = candidate.form_bindings
        self.plan = candidate.plan
        self.planned_tree = candidate.tree
        self.planned_environment = self._environment()
        self.dirty = self._runtime.dirty

    def commit(self, candidate: _Candidate[RenderTargetT]) -> None:
        """Commit application state, then publish the successfully delivered controls."""
        self._commit_application(candidate)
        self.assets = candidate.assets
        self._publish(candidate, MessageRootStatus.ACTIVE)

    def commit_presented(self, candidate: _Candidate[RenderTargetT]) -> None:
        self.commit(candidate)
        self._committed()
        self._presented()

    def same_as_live(self, candidate: _ApplicationCandidate[RenderTargetT]) -> bool:
        """Compare scenes, assets and action keys, allowing binding semantics to change.

        Scene fingerprints omit the control ids minted at draw time. A suppressed
        application commit updates bindings behind the existing live control ids.
        """
        plan = self.plan
        if plan is None or self.status is not MessageRootStatus.ACTIVE:
            return False
        if candidate.plan.report.scene_fingerprint != plan.report.scene_fingerprint:
            return False
        if candidate.assets != self.assets:
            return False
        return candidate.handlers.keys() == self.handlers.keys()

    def suppress(self, candidate: _ApplicationCandidate[RenderTargetT], profile: OperationRecorder | None) -> None:
        """Commit application state while retaining the visible generation and view."""
        self._commit_application(candidate)
        if isinstance(candidate, _Candidate):
            candidate.view.stop()
        self._committed()
        self.suppressed += 1
        if profile is not None:
            profile.increment("mount.suppressed", 1)
        logger.debug("mount %s: render identical to the live generation, edit suppressed", self._mount_id)

    def commit_renewal(self, candidate: _LifecycleCandidate) -> None:
        """Publish renewal controls while retaining the hidden application runtime."""
        self._settle(candidate, "commit renewal")
        self.handlers = candidate.handlers
        self.form_bindings = {}
        self.plan = candidate.rendered.plan
        self._publish(candidate, MessageRootStatus.RENEWAL_ARMED)
        self._presented()

    def discard(self, candidate: _SettlementCandidate[RenderTargetT]) -> None:
        """Release staged resources without applying proposed presentation updates."""
        self._settle(candidate, "roll back")
        if not isinstance(candidate, _PlannedCandidate):
            candidate.view.stop()
        if not isinstance(candidate, _LifecycleCandidate):
            candidate.render.discard()
        self.mark_dirty()

    def _publish(self, candidate: _Candidate[RenderTargetT] | _LifecycleCandidate, status: MessageRootStatus) -> None:
        self.generation = candidate.generation
        self.status = status
        candidate.view.timeout = self._remaining_timeout()
        if self.view is not None and self.view is not candidate.view:
            self.view.stop()
        self.view = candidate.view
        self._track()

    @contextmanager
    def delivery(self, candidate: _Candidate[RenderTargetT] | _LifecycleCandidate) -> Iterator[None]:
        """Treat a failed application edit as potentially visible until the next commit."""
        try:
            yield
        except BaseException:
            # A cancelled edit may have landed. Repainting before another exclusive action
            # is safer than letting the handler act on state the reader may not have seen.
            if isinstance(candidate, _Candidate):
                self.behind = True
            raise

    def close(self) -> None:
        """End any staged test render and stop the committed view."""
        self.supersede()
        if self.view is not None:
            view, self.view = self.view, None
            view.stop()
