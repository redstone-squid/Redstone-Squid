"""DurableBot startup ordering."""

from dataclasses import dataclass

import anyio
import discord

from squid_layouts.discord.durability import DurableBot, RecoveryReport


@dataclass(slots=True)
class StubRuntime:
    events: list[str]
    report: RecoveryReport

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        self.events.append("recover")
        task_status.started(self.report)
        await anyio.sleep_forever()


class StubBot(DurableBot):
    def __init__(self, runtime: StubRuntime, events: list[str]) -> None:
        super().__init__(command_prefix="!", intents=discord.Intents.none())
        self.runtime = runtime
        self.events = events

    def build_durable_runtime(self):
        self.events.append("build")
        return self.runtime

    async def login(self, token: str) -> None:
        assert token == "token"
        assert self.durable_sessions is self.runtime
        self.events.append("login")

    async def on_sessions_recovered(self, report: RecoveryReport) -> None:
        assert report is self.runtime.report
        self.events.append("hook")

    async def connect(self, *, reconnect: bool = True) -> None:
        assert reconnect is False
        assert self.recovery_report is self.runtime.report
        self.events.append("connect")


async def test_durable_bot_recovers_between_login_and_gateway_connect() -> None:
    events: list[str] = []
    report = RecoveryReport()
    bot = StubBot(StubRuntime(events, report), events)

    await bot.start("token", reconnect=False)

    assert events == ["build", "login", "recover", "hook", "connect"]
