"""Durable build-workspace actions and their custom IDs."""

from functools import partial
from uuid import UUID

from squid.bot.routes import AppRequest, ScreenUnavailable, router
from squid.bot.submission.ui import opening, recalculation
from squid.bot.tags_view import BuildTagsScreen
from squid.bot.utils.permissions import allows
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.core.i18n import tr
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING

builds = router.group("/builds")


@builds.route("/{build_id}/tags")
async def build_tags(request: AppRequest, build_id: int) -> BuildTagsScreen:
    services = request.client.services

    async def load_build() -> Build:
        build = await services.build_queries.get(build_id)
        if build is None or (
            build.submission_status is not Status.CONFIRMED and not await allows(request, BUILD_SUBMISSION_VIEW_PENDING)
        ):
            raise ScreenUnavailable(tr(t"navigation-build-unavailable = That build is unavailable."))
        return build

    build = await load_build()
    viewer = await request.client.actors.resolve(request)
    return BuildTagsScreen(
        build,
        account_id=viewer.account_id,
        load_build=load_build,
        load_tag=services.tags.public_definition,
        edits=services.edits,
        actor=partial(request.client.actors.resolve, request, refresh=True),
    )


@builds.route("/{build_id}/edit")
async def edit_build(request: AppRequest, build_id: int) -> None:
    """Open the build editor for the build a posted card points at."""
    await _open_editor(request, build_id)


@builds.route("/{build_id}/edit/recover")
async def recover_build_editor(request: AppRequest, build_id: int) -> None:
    """Reload current state and reauthorize a fresh editor after the previous one expired."""
    await _open_editor(request, build_id, recovered=True)


async def _open_editor(request: AppRequest, build_id: int, *, recovered: bool = False) -> None:
    build = await request.client.services.builds.get(build_id)
    if build is None:
        raise ScreenUnavailable(tr(t"build-editor-build-deleted = That build no longer exists."))
    await opening.open_build_editor(request, build, recovered=recovered)


@builds.route("/revision/{proposal_id}/reopen")
async def reopen_revision(request: AppRequest, proposal_id: UUID) -> recalculation.RecalculationProposalScreen:
    """Reopen a retained diff under the caller's current edit authority."""
    return await recalculation.RecalculationProposalScreen.load(request.client.services, proposal_id, request)


__all__ = [
    "builds",
    "edit_build",
    "recover_build_editor",
    "reopen_revision",
]
