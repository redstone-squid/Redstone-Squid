"""Durable build-workspace actions and their custom IDs."""

from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

from discord import Interaction

import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.routes import routes
from squid.bot.submission.ui import opening, recalculation
from squid.bot.tags_view import TagsScreen
from squid.bot.ui import text_node
from squid.bot.utils.permissions import allows
from squid.core.i18n import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_LIST,
    TAG_PROPOSAL_REJECT,
)

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


builds = routes.group("builds")


@builds.route("{build_id:int}:tags")
async def build_tags(interaction: Interaction[RedstoneSquid], build_id: int) -> None:
    request = await sd.request(interaction)
    services = interaction.client.services
    account_id = await ensure_consented_account(request, services.accounts)
    if account_id is None:
        return

    async def authorize(node: PermissionNode) -> bool:
        return await allows(request, node)

    capabilities = frozenset(
        [
            node
            for node in (
                RESTRICTION_ALIAS_CREATE,
                TAG_PROPOSAL_APPROVE,
                TAG_PROPOSAL_ARCHIVE,
                TAG_PROPOSAL_LIST,
                TAG_PROPOSAL_REJECT,
            )
            if await authorize(node)
        ]
    )
    await request.respond(
        TagsScreen(
            services.tags,
            services.restrictions,
            services.edits,
            partial(request.client.actors.resolve, request, refresh=True),
            build_id=build_id,
            actor_account_id=account_id,
            capabilities=capabilities,
            authorize=authorize,
        )
    )


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


@builds.route("revision:{proposal_id}:reopen")
async def reopen_revision(interaction: Interaction[RedstoneSquid], proposal_id: str) -> None:
    """Reopen a retained diff under the caller's current edit authority."""
    request = await sd.request(interaction)
    screen = await recalculation.RecalculationProposalScreen.load(
        interaction.client.services, UUID(proposal_id), request
    )
    await request.respond(screen, audience="personal")


__all__ = [
    "builds",
    "edit_build",
    "recover_build_editor",
    "reopen_revision",
]
