"""Durable build-workspace actions and their custom IDs."""

from functools import partial
from uuid import UUID

from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.routes import AppRequest, router
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

builds = router.group("/builds")


@builds.route("/{build_id}/tags")
async def build_tags(request: AppRequest, build_id: int) -> None:
    services = request.client.services
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


@builds.route("/{build_id}/edit")
async def edit_build(request: AppRequest, build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    build = await request.client.services.builds.get(build_id)
    if build is None:
        await request.respond(
            text_node(tr(t"build-editor-build-deleted = That build no longer exists.")),
            audience="personal",
        )
        return
    await opening.open_build_editor(request, build)


@builds.route("/{build_id}/edit/recover")
async def recover_build_editor(request: AppRequest, build_id: int) -> None:
    """Reload current state and reauthorize a fresh editor after the previous one expired."""
    build = await request.client.services.builds.get(build_id)
    if build is None:
        await request.respond(
            text_node(tr(t"build-editor-build-deleted = That build no longer exists.")),
            audience="personal",
        )
        return
    await opening.open_build_editor(request, build, recovered=True)


@builds.route("/revision/{proposal_id}/reopen")
async def reopen_revision(request: AppRequest, proposal_id: UUID) -> None:
    """Reopen a retained diff under the caller's current edit authority."""
    screen = await recalculation.RecalculationProposalScreen.load(request.client.services, proposal_id, request)
    await request.respond(screen, audience="personal")


__all__ = [
    "builds",
    "edit_build",
    "recover_build_editor",
    "reopen_revision",
]
