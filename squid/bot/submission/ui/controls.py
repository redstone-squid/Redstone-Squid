"""Durable build-workspace actions and their custom IDs."""

from typing import TYPE_CHECKING
from uuid import UUID

from discord import Interaction

import squid_ui_discord as sd
from squid.bot.consent import ensure_consented_account
from squid.bot.routes._root import _feature_group
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.ui import drafts, inference, opening, revisions
from squid.bot.ui import text_node
from squid.bot.utils.permissions import actor_for_interaction
from squid.core.i18n import tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


builds, _builds_created = _feature_group("builds")


@builds.route("{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))
async def edit_build(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    request = await sd.request(interaction)
    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        await request.respond(
            text_node(tr(t"build-editor-build-deleted = That build no longer exists.")),
            audience="personal",
        )
        return
    await opening.open_build_editor(request, build)


@builds.route("{build_id:int}:edit:recover")
async def recover_build_editor(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    """Reload current state and reauthorize a fresh editor after the previous one expired."""
    request = await sd.request(interaction)
    build = await interaction.client.services.builds.get(build_id)
    if build is None:
        await request.respond(
            text_node(tr(t"build-editor-build-deleted = That build no longer exists.")),
            audience="personal",
        )
        return
    await opening.open_build_editor(request, build, recovered=True)


@builds.route("draft:{draft_id}:reopen")
async def reopen_draft(interaction: Interaction[RedstoneSquid], draft_id: str) -> None:
    """Resolve durable identity and current authority before reopening a private draft."""
    request = await sd.request(interaction)
    actor = await ensure_consented_account(request, interaction.client.services.accounts)
    if actor is None:
        return
    screen = await drafts.draft_editor(
        interaction.client.services,
        actor,
        UUID(draft_id),
        changes=interaction.client.topic_bus.topic("submission_draft", draft_id),
        render_card=lambda build: render_build_card(build, interaction.client.media_previews),
    )
    await request.respond(
        screen, audience="personal", session_key=sd.SessionKey.custom("draft", (interaction.user.id, draft_id))
    )


@builds.route("revision:{proposal_id}:reopen")
async def reopen_revision(interaction: Interaction[RedstoneSquid], proposal_id: str) -> None:
    """Reopen a retained diff under the caller's current edit authority."""
    request = await sd.request(interaction)
    screen = await revisions.proposal_screen(interaction.client.services, UUID(proposal_id), request)
    await request.respond(screen, audience="personal")


@builds.route("inference:{run_id}:reopen")
async def reopen_inference(interaction: Interaction[RedstoneSquid], run_id: str) -> None:
    """Recover candidates under current consent and owner or staff authority."""
    request = await sd.request(interaction)
    if await ensure_consented_account(request, interaction.client.services.accounts) is None:
        return
    actor = await actor_for_interaction(request)
    candidates = await interaction.client.services.submission_inference.get(UUID(run_id), actor)
    await request.respond(
        inference.InferenceCandidatesScreen(interaction.client.services, UUID(run_id), candidates), audience="personal"
    )


__all__ = [
    "builds",
    "edit_build",
    "recover_build_editor",
    "reopen_draft",
    "reopen_inference",
    "reopen_revision",
]
