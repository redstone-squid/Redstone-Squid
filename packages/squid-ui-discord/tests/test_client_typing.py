"""Client identity survives request factories and rejects unrelated applications."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SOURCE = """\
from typing import Any, Self, assert_type
import discord
from discord.ext import commands
import squid_ui as sl
import squid_ui_discord as sd

class AppBot(commands.Bot):
    services: str

class OtherBot(commands.Bot):
    other: str

class Owner:
    pass

class Cog(sd.Cog[AppBot]):
    @sd.command()
    async def inspect(self, request: sd.Request[Self, AppBot]) -> str:
        assert_type(request.client, AppBot)
        assert_type(request.owner, Self)
        return request.client.services

    @sd.hybrid_command()
    async def hybrid(self, request: sd.Request[Self, AppBot]) -> str:
        return request.client.services

    @sd.prefix_command()
    async def prefix(self, request: sd.Request[Self, AppBot]) -> str:
        return request.client.services

    @sd.autocomplete()
    async def complete(self, request: sd.Request[Self, AppBot], value: str) -> list[tuple[str, str]]:
        return [(request.client.services, value)]

def needs_app(request: sd.Request[Any, AppBot]) -> str:
    return request.client.services

async def check(
    bot: AppBot,
    cog: Cog,
    other: OtherBot,
    interaction: discord.Interaction[AppBot],
    context: commands.Context[AppBot],
    untyped: discord.Interaction[discord.Client],
    event: sl.PressEvent,
    press: sd.PressEvent[AppBot],
    screen: sd.Screen[None, Owner, AppBot],
) -> None:
    runtime = sd.DiscordUIRuntime.of(bot)
    assert_type(runtime, sd.DiscordUIRuntime[AppBot])
    assert_type(sd.DiscordUIRuntime.of(interaction), sd.DiscordUIRuntime[AppBot])
    assert_type(sd.DiscordUIRuntime.of(context), sd.DiscordUIRuntime[AppBot])
    assert_type(runtime.app, sd.Scope[AppBot, AppBot])
    scope = runtime.scope(Owner())
    assert_type(scope, sd.Scope[Owner, AppBot])
    assert_type(runtime.scope_for(scope.owner), sd.Scope[Owner, AppBot])
    assert_type(cog.ui, sd.Scope[Cog, AppBot])
    request = await scope.request(interaction)
    assert_type(request, sd.Request[Owner, AppBot])
    assert_type(request.client, AppBot)
    assert_type(request.interaction, discord.Interaction[AppBot] | None)
    assert_type(await sd.request(interaction), sd.Request[Any, AppBot])
    assert_type(await sd.request(context), sd.Request[Any, AppBot])
    assert_type(await sd.request(event), sd.Request[Any, discord.Client])
    assert_type(await scope.request(event), sd.Request[Owner, AppBot])
    assert_type(press.context.client, AppBot)
    assert_type(screen.opening, sd.Request[Owner, AppBot] | None)
    needs_app(request)
    general: sd.Request[Any, discord.Client] = request
    needs_app(await sd.request(untyped))  # reject
    needs_app(await sd.DiscordUIRuntime.of(other).app.request(event))  # reject
    request.client = other  # reject
    runtime.client = other  # reject
    request.runtime = sd.DiscordUIRuntime.of(other)  # reject
    request.scope = sd.DiscordUIRuntime.of(other).app  # reject
"""


def test_client_types_survive_dispatch(tmp_path: Path) -> None:
    checker = shutil.which("pyrefly")
    if checker is None:
        pytest.skip("requires the development type checker")
    source = tmp_path / "client_types.py"
    source.write_text(_SOURCE)
    config = Path(__file__).resolve().parents[3] / "pyproject.toml"
    result = subprocess.run(
        [checker, "check", "--config", str(config), "--output-format=min-text", str(source)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    expected = {index for index, line in enumerate(_SOURCE.splitlines(), 1) if "# reject" in line}
    actual = {int(line) for line in re.findall(r"client_types\.py:(\d+):", output)}
    assert result.returncode == 1, output
    assert actual == expected, output
