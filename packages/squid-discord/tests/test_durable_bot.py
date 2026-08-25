"""DurableBot startup ordering."""

from dataclasses import dataclass

import anyio
import discord
import pytest
from discord.ext import commands

from squid_discord.durability import DurableBot, DurableSessionRuntime, RecoveryReport


@dataclass(slots=True)
class StubRuntime(DurableSessionRuntime):
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

    def build_durable_runtime(self) -> DurableSessionRuntime:
        self.events.append("build")
        return self.runtime

    async def on_sessions_recovered(self, report: RecoveryReport) -> None:
        assert report is self.runtime.report
        self.events.append("hook")


@pytest.fixture
def stub_discord_lifecycle(monkeypatch: pytest.MonkeyPatch, events: list[str]):
    async def login(bot: commands.Bot, token: str) -> None:
        assert token == "token"
        assert isinstance(bot, StubBot)
        assert bot.durable_sessions is bot.runtime
        events.append("login")

    async def connect(bot: commands.Bot, *, reconnect: bool = True) -> None:
        assert reconnect is False
        assert isinstance(bot, StubBot)
        assert bot.recovery_report is bot.runtime.report
        events.append("connect")

    monkeypatch.setattr(commands.Bot, "login", login)
    monkeypatch.setattr(commands.Bot, "connect", connect)


@pytest.fixture
def events() -> list[str]:
    return []


async def test_explicit_login_then_connect_recovers_before_gateway(
    stub_discord_lifecycle: None,
    events: list[str],
) -> None:
    report = RecoveryReport()
    bot = StubBot(StubRuntime(events, report), events)

    await bot.login("token")
    assert events == ["build", "login"]

    await bot.connect(reconnect=False)

    assert events == ["build", "login", "recover", "hook", "connect"]


async def test_inherited_start_uses_the_same_durable_connect_path(
    stub_discord_lifecycle: None,
    events: list[str],
) -> None:
    report = RecoveryReport()
    bot = StubBot(StubRuntime(events, report), events)

    await bot.start("token", reconnect=False)

    assert events == ["build", "login", "recover", "hook", "connect"]
