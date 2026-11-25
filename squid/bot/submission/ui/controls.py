"""Durable build-workspace actions and their custom IDs."""

from uuid import UUID

from squid.bot.routes import AppRequest, AppRoutes, ScreenUnavailable
from squid.bot.submission.ui import opening, recalculation
from squid.core.actors import ActingContext
from squid.core.i18n import tr

router = AppRoutes()


builds = router.group("/builds")


@builds.route("/{build_id}/edit")
async def edit_build(request: AppRequest, build_id: int, session: UUID | None = None) -> None:
    """Open the build editor for the build a posted card points at."""
    await _open_editor(request, build_id, session=session)


@builds.route("/{build_id}/edit/recover")
async def recover_build_editor(request: AppRequest, build_id: int, session: UUID | None = None) -> None:
    """Reload current state and reauthorize a fresh editor after the previous one expired."""
    await _open_editor(request, build_id, recovered=True, session=session)


async def _open_editor(
    request: AppRequest, build_id: int, *, recovered: bool = False, session: UUID | None = None
) -> None:
    if session is not None:
        await request.client.actors.request_actor(request, session, f"build:{build_id}")
    build = await request.client.services.builds.get(build_id, context=ActingContext.current())
    if build is None:
        raise ScreenUnavailable(tr(t"build-editor-build-deleted = That build no longer exists."))
    await opening.open_build_editor(request, build, recovered=recovered, session=session)


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
