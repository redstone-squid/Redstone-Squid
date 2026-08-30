"""Durable routed controls for build workspaces."""

from typing import TYPE_CHECKING

from discord import Interaction

import squid_ui_discord as sd
from squid.bot.routes._root import _feature_group, _feature_route
from squid.bot.ui import text_node
from squid.core.i18n import tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


builds, _builds_created = _feature_group("builds")
build_edit = _feature_route(builds, "{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))


@builds.route(build_edit)
async def edit_build(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    from squid.bot.submission.ui.opening import open_build_editor

    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        await interaction.client.app_ui.respond(
            interaction,
            text_node(tr("That build no longer exists.")),
            audience="personal",
        )
        return
    await open_build_editor(interaction, build, interaction.client.app_ui)


__all__ = ["build_edit", "builds", "edit_build"]
