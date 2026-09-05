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
build_edit_recovery = _feature_route(builds, "{build_id:int}:edit:recover")


@builds.route(build_edit)
async def edit_build(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    from squid.bot.submission.ui.opening import open_build_editor

    request = await sd.request(interaction)
    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        await request.respond(text_node(tr(t"That build no longer exists.")), audience="personal")
        return
    await open_build_editor(request, build)


@builds.route(build_edit_recovery)
async def recover_build_editor(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Reload current state and reauthorize a fresh editor after the previous one expired."""
    from squid.bot.submission.ui.opening import open_build_editor

    request = await sd.request(interaction)
    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        await request.respond(text_node(tr(t"That build no longer exists.")), audience="personal")
        return
    await open_build_editor(request, build, recovered=True)


__all__ = ["build_edit", "build_edit_recovery", "builds", "edit_build", "recover_build_editor"]
