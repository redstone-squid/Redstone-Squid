"""Showing a guard's challenge, and running the press the actor approves.

Two halves that a mount cannot supply for itself. It holds no session registry -- the lookup
runs the other way -- so it cannot open the dialog; and it has no task of its own that
predates a press, so it cannot run the resumption anywhere safe. Both are host-supplied,
through `MountDefaults(challenge=...)`.

The supervisor half is not a convenience. `transaction()` flattens rather than nests, so a
press resumed from inside the approving handler would stage its writes in the dialog's
overlay and commit with it, and a `PARALLEL_READ` press would raise outright. A task spawned
from that handler inherits the same context. Only work handed across a queue to a task
started earlier escapes it, which is what `ChallengeRunner` is.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from squid_layouts.discord.delivery import respond_to
from squid_layouts.discord.mount import ChallengeRequest, ChallengeSupervisor, ResumedPress
from squid_layouts.discord.screens import Opener, Screen
from squid_layouts.discord.sessions import SessionRegistry
from squid_layouts.guards import ChallengeResolver

logger = logging.getLogger(__name__)

CHALLENGE_SCREEN = Screen("challenge")
"""Owner-only by default, so only the actor who was asked can answer.

Its session key rarely decides anything: a challenge is opened with `parent=`, so it attaches
to the panel's own session and dies with it.
"""


class ChallengeRunner:
    """Runs approved presses in a task whose context predates the press that approved it.

    `resume` is a plain queue push and copies no context, so it is safe to call from inside
    the dialog's transaction; `run` is the host's background task that drains it. Started
    once, alongside the reactor:

    ```python
    background.start(runner.run(), name="layout-challenges")
    ```

    Without a running drain, approvals queue silently and nothing resumes -- which is the
    honest failure for a host that wired the presenter but forgot the task.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        self._queue: asyncio.Queue[ResumedPress] = asyncio.Queue(maxsize=capacity)
        self._running = False

    def resume(self, press: ResumedPress) -> None:
        """Queue an approved press. Never blocks, and never runs it here."""
        try:
            self._queue.put_nowait(press)
        except asyncio.QueueFull:
            # Dropped rather than awaited: this is called from a handler holding an open
            # transaction, and the actor can press again.
            logger.warning("challenge runner is full; an approved press was dropped")

    async def run(self) -> None:
        """Serve approved presses until the host cancels this coroutine."""
        if self._running:
            message = "challenge runner is already running"
            raise RuntimeError(message)
        self._running = True
        try:
            async with asyncio.TaskGroup() as tasks:
                while True:
                    press = await self._queue.get()
                    # Started from this task, not from the approving one: the resumed press
                    # inherits the context this loop was created in, which has no transaction.
                    tasks.create_task(self._carry(press))
        finally:
            self._running = False

    @staticmethod
    async def _carry(press: ResumedPress) -> None:
        try:
            await press()
        except Exception:
            # A resumed press routes its own application failures through the mount's error
            # hook, so anything arriving here is the framework's problem, not the actor's.
            logger.exception("a resumed press failed outside its own error handling")


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
    """Asks the guard's question as an ephemeral panel attached to the one that asked.

    A child mount rather than a modal: modals cannot be opened from an interaction whose
    response has been spent, and a challenge is answered by controls the actor may take a
    minute over.
    """

    sessions: SessionRegistry
    supervisor: ChallengeSupervisor
    screen: Screen = field(default=CHALLENGE_SCREEN)

    async def present(self, request: ChallengeRequest) -> None:
        """Open the dialog through the interaction that asked, and return."""
        resolver: ChallengeResolver = _Resolver(request, self.supervisor)
        await self.screen.open(
            self.sessions,
            request.challenge.ask(resolver),
            respond_to(request.interaction, ephemeral=True, wait=True),
            opener=Opener.of(request.interaction),
            parent=request.mount,
            # Locale is a fact about the reader, not about the host, so the question is asked
            # in the language the panel that asked it is speaking -- chrome labels included.
            localization=request.mount.localization,
            # The deadline is the dialog's whole life: an unanswered challenge dies with its
            # mount, having written no approval, which is a decline with nothing to undo.
            timeout=request.challenge.deadline,
        )


__all__ = ["CHALLENGE_SCREEN", "ChallengeRunner", "DialogPresenter"]
