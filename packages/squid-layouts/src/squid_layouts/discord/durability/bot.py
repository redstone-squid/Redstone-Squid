"""A commands.Bot lifecycle with durable-session recovery ordering."""

import anyio
from discord.ext import commands

from .runtime import DurableSessionRuntime, RecoveryReport


class DurableBot(commands.Bot):
    """A bot that recovers durable sessions between login and gateway connect.

    Subclasses implement :meth:`build_durable_runtime`. The runtime is created
    before ``login()`` so ``setup_hook()`` and cogs can use ``durable_sessions``.
    Recovery then completes before the gateway starts dispatching interactions.
    """

    _durable_sessions: DurableSessionRuntime | None = None
    recovery_report: RecoveryReport | None = None

    def build_durable_runtime(self) -> DurableSessionRuntime:
        """Construct this bot's durable-session runtime once.

        Returns:
            The configured runtime, normally using ``DiscordFrontend(self)``.
        """
        message = f"{type(self).__name__} must implement build_durable_runtime()"
        raise NotImplementedError(message)

    @property
    def durable_sessions(self) -> DurableSessionRuntime:
        """The lazily constructed runtime available during ``setup_hook()``."""
        if self._durable_sessions is None:
            self._durable_sessions = self.build_durable_runtime()
        return self._durable_sessions

    async def on_sessions_recovered(self, report: RecoveryReport) -> None:
        """Run after recovery is ready and before gateway interaction dispatch."""

    async def login(self, token: str) -> None:
        """Authenticate after making the runtime available to ``setup_hook()``."""
        _ = self.durable_sessions
        await super().login(token)

    async def connect(self, *, reconnect: bool = True) -> None:
        """Recover under supervision before connecting to the gateway."""
        runtime = self.durable_sessions
        async with anyio.create_task_group() as tasks:
            report = await tasks.start(runtime.run)
            self.recovery_report = report
            await self.on_sessions_recovered(report)
            try:
                await super().connect(reconnect=reconnect)
            finally:
                tasks.cancel_scope.cancel()
