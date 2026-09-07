"""Durable routed controls for build workspaces."""

from typing import TYPE_CHECKING
from uuid import UUID

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
draft_reopen = _feature_route(builds, "draft:{draft_id}:reopen")
revision_reopen = _feature_route(builds, "revision:{proposal_id}:reopen")


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


@builds.route(draft_reopen)
async def reopen_draft(interaction: Interaction[RedstoneSquid], draft_id: str) -> None:
    """Resolve durable identity and current authority before reopening a private draft."""
    from squid.bot.consent import ensure_consented_account
    from squid.bot.submission.ui.drafts import draft_editor

    request = await sd.request(interaction)
    actor = await ensure_consented_account(request, interaction.client.services.accounts)
    if actor is None:
        return
    screen = await draft_editor(interaction.client.services, actor, UUID(draft_id))
    await request.respond(
        screen, audience="personal", session_key=sd.SessionKey.custom("draft", (interaction.user.id, draft_id))
    )


@builds.route(revision_reopen)
async def reopen_revision(interaction: Interaction[RedstoneSquid], proposal_id: str) -> None:
    """Reopen a retained diff under the caller's current edit authority."""
    from squid.bot.submission.ui.revisions import proposal_screen

    request = await sd.request(interaction)
    screen = await proposal_screen(interaction.client.services, UUID(proposal_id), request)
    await request.respond(screen, audience="personal")
