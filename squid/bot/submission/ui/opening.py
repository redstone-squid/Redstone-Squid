"""Discord transport boundary for opening build-edit screens."""

from typing import TYPE_CHECKING, Any, cast

import squid_ui_discord as sd
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.ui.build_edit import BuildEditScreen, BuildEditWorkspace
from squid.bot.ui import error_node, tr
from squid.bot.utils.permissions import current_edit_actor
from squid.builds.application import BuildService
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.submissions.domain import ChoiceOption

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


async def prepare_build_editor(
    request: sd.Request[Any],
    build: Build,
    builds: BuildService | None = None,
    *,
    recovered: bool = False,
) -> BuildEditScreen:
    """Construct an editor and its callbacks without presenting or authorizing it yet.

    show_build_editor checks access before presentation. The callback also lets the
    screen recheck permission, allowing a pending build's submitter or an account
    with build-edit permission to proceed.
    """
    client = cast("RedstoneSquid", request.client)
    prepared: BuildEditScreen | None = None

    async def actor() -> Actor:
        return await current_edit_actor(request)

    async def authorize() -> bool:
        current = build if prepared is None else prepared.build
        current_actor = await actor()
        actor_account_id = current_actor.account_id
        if (
            current.submission_status is Status.PENDING
            and actor_account_id is not None
            and current.submitter_account_id == actor_account_id
        ):
            return True
        return (await client.services.permissions.check(current_actor, BUILD_SUBMISSION_EDIT)).allowed

    async def render_build(current: Build) -> Any:
        return await render_build_card(
            current, client.media_previews, schematic_summaries=client.services.schematic_summaries
        )

    async def refresh_posts(build_id: int) -> None:
        await client.refresh_posts("build", str(build_id))

    forms = client.services.submission_forms
    manifest = await forms.edit_manifest(locale=None)
    category = build.category.value.lower()

    async def load_options(source: str) -> tuple[ChoiceOption, ...]:
        return (await forms.options(source, category, locale=None)).options

    # Load every catalogue now: the restriction and pattern catalogues decide which of the build's
    # names are resolved, and the others label its saved answers in the overview.
    sources = sorted({field.option_source for field in manifest.fields_for(category) if field.option_source})
    workspace = BuildEditWorkspace(
        build, manifest, {source: await load_options(source) for source in sources}, load_options=load_options
    )
    prepared = BuildEditScreen(
        build,
        client.services.builds if builds is None else builds,
        workspace,
        node=await render_build(build),
        authorize=authorize,
        edits=client.services.edits,
        actor=actor,
        render_build=render_build,
        refresh_posts=refresh_posts,
        changes=client.topic_bus.topic("build", str(build.id)),
        recovered=recovered,
    )
    return prepared


async def show_build_editor(request: sd.Request[Any], screen: BuildEditScreen) -> BuildEditScreen | None:
    """Authorize and show a prepared editor under its user/build key."""
    if not await screen.may_edit():
        await request.respond(
            error_node(
                tr(t"bot-submission-ui-opening-cannot-edit-this-build = Cannot edit this build"),
                tr(
                    t"build-editor-edit-access-required = Only the pending "
                    t"build's submitter or a trusted staff member can edit it."
                ),
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
