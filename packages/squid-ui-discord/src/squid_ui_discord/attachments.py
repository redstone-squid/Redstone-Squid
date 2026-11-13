"""Turning a plan's declarative assets into Discord files.

Shared by every path that can carry an attachment — a mount's delivery, a sessionless
composition, and a fragment contributed to someone else's view — so a file behaves the same
way wherever it was planned.
"""

import io
from collections.abc import Sequence

import discord

from squid_ui.assets import Asset
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.result import PlanResult


def attachment_assets(plan: PlanResult) -> tuple[Asset, ...]:
    """The assets a plan expects to be uploaded, excluding ones already served by URL."""
    assets = tuple(
        asset
        for scene_asset in plan.scene.assets
        if isinstance(asset := plan.resources.get(f"asset:{scene_asset.key}"), Asset) and asset.url is None
    )

    names: set[str] = set()
    for asset in assets:
        if asset.name in names:
            message = f"different attachments share filename {asset.name!r}; give them distinct filename= values"
            raise LayoutInvariantError(message)
        names.add(asset.name)
    return assets


def files_for(assets: Sequence[Asset]) -> list[discord.File]:
    """Materialize fresh `discord.File` wrappers; a sent file cannot be re-sent."""
    files: list[discord.File] = []
    try:
        files.extend(discord.File(io.BytesIO(asset.read()), filename=asset.name) for asset in assets)
    except BaseException:
        for file in files:
            file.close()
        raise
    return files
