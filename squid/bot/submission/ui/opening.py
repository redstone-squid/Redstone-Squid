"""Discord transport boundary for opening build-edit screens."""

from typing import TYPE_CHECKING, Any, cast

import squid_ui_discord as sd
from squid.bot.submission.ui.views import BuildEditScreen
from squid.bot.ui import error_node, tr
from squid.bot.utils.permissions import allows
from squid.builds.application import BuildService
from squid.builds.domain import Build, Status
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


async def prepare_build_editor(
    request: sd.Request[Any],
    build: Build,
    builds: BuildService | None = None,
    *,
    recovered: bool = False,
) -> BuildEditScreen:
    """Inject actor-aware Discord operations into one build editor."""
    client = cast("RedstoneSquid", request.client)
    actor_id = request.user.id
    prepared: BuildEditScreen | None = None

    async def authorize() -> bool:
        current = build if prepared is None else prepared.build
        actor_account_id = await client.account_ids.resolve(client.services.accounts, actor_id)
        if (
            current.submission_status is Status.PENDING
            and actor_account_id is not None
            and current.submitter_account_id == actor_account_id
        ):
            return True
        return await allows(request, BUILD_SUBMISSION_EDIT)

    async def render_build(current: Build) -> Any:
        return await client.for_build(current).render_node()

    async def refresh_posts(build_id: int) -> None:
        await client.refresh_posts("build", str(build_id))

    prepared = BuildEditScreen(
        build,
        client.services.builds if builds is None else builds,
        node=await render_build(build),
        authorize=authorize,
        render_build=render_build,
        refresh_posts=refresh_posts,
        recovered=recovered,
    )
    return prepared


async def show_build_editor(request: sd.Request[Any], screen: BuildEditScreen) -> BuildEditScreen | None:
    """Authorize and show a prepared editor under its user/build key."""
    if not await screen.may_edit():
        await request.respond(
            error_node(
                tr(t"Cannot edit this build"),
                tr(t"Only the pending build's submitter or a trusted staff member can edit it."),
            ),
            audience="personal",
        )
        return None
    key = sd.SessionKey.custom("build-edit", (request.user.id, screen.build.id))
    outcome = await request.respond(screen, session_key=key)
    return screen if isinstance(outcome, sd.Presented) else None


async def open_build_editor(
    request: sd.Request[Any],
    build: Build,
    *,
    recovered: bool = False,
) -> BuildEditScreen | None:
    """Prepare and show a build editor in one call."""
    return await show_build_editor(
        request,
        await prepare_build_editor(request, build, recovered=recovered),
    )


__all__ = ["open_build_editor", "prepare_build_editor", "show_build_editor"]
