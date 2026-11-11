"""Decorated route handles retain handler signatures and expose ID generation."""

from collections.abc import Awaitable, Callable
from typing import assert_type

import discord

from squid_ui.routing import Route
from squid_ui_discord.routing import RouteGroup, Router

router = Router[discord.Client]()
builds = RouteGroup[discord.Client]("builds")


@builds.route("{build_id:int}:edit")
async def edit(interaction: discord.Interaction[discord.Client], build_id: int) -> None: ...


@router.select("{build_id:int}:pick")
async def pick(interaction: discord.Interaction[discord.Client], values: tuple[str, ...], *, build_id: int) -> None: ...


edit_callback: Callable[[discord.Interaction[discord.Client], int], Awaitable[None]] = edit
assert_type(edit.id(build_id=5), str)
assert_type(edit.route, Route)


async def call_handles(interaction: discord.Interaction[discord.Client]) -> None:
    assert_type(await edit(interaction, build_id=5), None)
    assert_type(await pick(interaction, ("choice",), build_id=5), None)
