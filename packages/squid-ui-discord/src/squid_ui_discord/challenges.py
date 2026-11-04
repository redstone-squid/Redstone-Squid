"""Present guard challenges and resume approved presses in host-owned tasks.

A guard can ask the actor to confirm before an action runs. DialogPresenter attaches
that question to the parent's session; ChallengeRunner resumes the action after approval.
The host starts the runner before dispatching interactions.

Approvals cross a queue because nested transactions share the outer transaction. Running
the action in the approving handler would commit its changes with the dialog's changes.
Even a newly spawned task inherits that transaction context, so the consumer must already
be running when the approval arrives.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import anyio

from squid_ui.guards import ChallengeResolver
from squid_ui_discord.delivery import respond_to
from squid_ui_discord.message_root_contracts import ChallengeRequest, ChallengeSupervisor, ResumedPress
from squid_ui_discord.session_specs import OpenContext, SessionSpec
from squid_ui_discord.sessions import SessionManager

logger = logging.getLogger(__name__)

CHALLENGE_SESSION_SPEC = SessionSpec("challenge")
"""Owner-only by default, so only the actor who was asked can answer.

Its session key rarely decides anything: a challenge uses the strict attachment entry point, so it attaches
to the panel's own session and dies with it.
"""


class ChallengeRunner:
    """Run approved presses from the context in which the host started ``run``.

    Start ``run`` in the host task group before handling interactions. ``resume`` only
    queues work; without a running consumer, approvals remain queued. ``concurrency``
    limits running presses and ``capacity`` limits the queue. The consumer may also hold
    one dequeued press while waiting for a concurrency slot.

    A full queue drops new approvals with a warning. Presses have no automatic execution
    timeout, and cancellation of the runner cancels its running presses.
    """

    def __init__(self, *, capacity: int = 256, concurrency: int = 16) -> None:
        if capacity <= 0 or concurrency <= 0:
            message = "challenge runner capacity and concurrency must be positive"
            raise ValueError(message)
        self._queue: asyncio.Queue[ResumedPress] = asyncio.Queue(maxsize=capacity)
        self._limit = asyncio.Semaphore(concurrency)
        self._active = 0
        self._running = False

    @property
    def active_count(self) -> int:
        """Return how many approved presses are running right now."""
        return self._active

    def resume(self, press: ResumedPress) -> None:
        """Queue an approved press. Never blocks, and never runs it here."""
        try:
            self._queue.put_nowait(press)
        except asyncio.QueueFull:
            # Waiting here would hold the approving handler's transaction open.
            # The actor can retry when the queue has room.
            logger.warning("challenge runner is full; an approved press was dropped")

    async def run(self) -> None:
        """Serve approved presses until the host cancels this coroutine."""
        if self._running:
            message = "challenge runner is already running"
            raise RuntimeError(message)
        self._running = True
        try:
            async with anyio.create_task_group() as tasks:
                while True:
                    press = await self._queue.get()
                    # Taken before the task is started, so the loop stops dequeuing while
                    # saturated. Without it the queue bounds nothing: every approval was
                    # dequeued and started immediately, so N approvals meant N presses at once.
                    await self._limit.acquire()
                    # Started from this task, not from the approving one: the resumed press
                    # inherits the context this loop was created in, which has no transaction.
                    tasks.start_soon(self._carry, press)
        finally:
            self._running = False

    async def _carry(self, press: ResumedPress) -> None:
        """Run one queued press while accounting for its concurrency slot."""
        self._active += 1
        try:
            await press()
        except Exception:
            # A resumed press routes its own application failures through the mount's error
            # hook, so anything arriving here is the framework's problem, not the actor's.
            logger.exception("a resumed press failed outside its own error handling")
        finally:
            self._active -= 1
            self._limit.release()


@dataclass(slots=True)
class _Resolver:
    """One challenge's answer, latched so a raced second click changes nothing."""

    request: ChallengeRequest
    supervisor: ChallengeSupervisor
    answered: bool = False

    async def approve(self) -> None:
        if self.answered:
            return
        self.answered = True
        self.supervisor.resume(self.request.approve)

    async def decline(self) -> None:
        if self.answered:
            return
        self.answered = True
        await self.request.decline()


@dataclass(frozen=True, slots=True)
class DialogPresenter:
    """Show an ephemeral question attached to its parent's registered session.

    The supplied session manager must already know the parent mount. A standalone mount
    cannot acquire a challenge child through this presenter. The dialog is owner-only and
    ends with its parent or its own deadline. A child panel can be sent after the original
    interaction has been acknowledged; a modal would require its unused initial response.
    """

    sessions: SessionManager
    supervisor: ChallengeSupervisor
    session_spec: SessionSpec = field(default=CHALLENGE_SESSION_SPEC)

    async def present(self, request: ChallengeRequest) -> None:
        """Open the dialog through the interaction that asked, and return."""
        resolver: ChallengeResolver = _Resolver(request, self.supervisor)
        await self.session_spec.attach(
            request.challenge.ask(resolver),
            respond_to(request.interaction, ephemeral=True, wait=True),
            sessions=self.sessions,
            open_context=OpenContext.of(request.interaction),
            parent=request.message_root,
            # Locale is a fact about the reader, not about the host, so the question is asked
            # in the language the panel that asked it is speaking -- chrome labels included.
            localization=request.message_root.localization,
            # The deadline is the dialog's whole life: an unanswered challenge dies with its
            # mount, having written no approval, which is a decline with nothing to undo.
            timeout=request.challenge.deadline,
        )


__all__ = ["CHALLENGE_SESSION_SPEC", "ChallengeRunner", "DialogPresenter"]
