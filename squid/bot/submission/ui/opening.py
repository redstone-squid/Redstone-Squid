"""Discord transport boundary for opening build-edit screens."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

import squid_ui.ext.discord as sd
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.ui.build_edit import BuildEditScreen, BuildEditWorkspace
from squid.bot.ui import error_node, tr
from squid.core.actors import ActingContext, Actor
from squid.core.builds.application import BuildService
from squid.core.builds.domain import EditAccess
from squid.core.builds.models import Build
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.core.submissions.domain import ChoiceOption

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


async def prepare_build_editor(
    request: sd.Request[Any, RedstoneSquid],
    build: Build,
    builds: BuildService | None = None,
    *,
    recovered: bool = False,
    session: UUID | None = None,
) -> BuildEditScreen:
    """Construct an editor and its callbacks without presenting or authorizing it yet.

    show_build_editor checks access before presentation. Moderators can edit attribution.
    """
    client = request.client
    prepared: BuildEditScreen | None = None

    async def actor() -> Actor:
        return await request.client.actors.request_actor(request, session, f"build:{build.id}")

    async def access(current: Build) -> EditAccess:
        current_actor = await actor()
        may_edit_any = (await client.services.permissions.check(current_actor, BUILD_SUBMISSION_EDIT)).allowed
        return current.edit_access(current_actor.linked_account_id, may_edit_any=may_edit_any)

    # The editor keeps the scope it opened with; losing full access closes it rather than narrowing it.
    scope = await access(build)
    context = ActingContext.for_actor(await actor())
    is_moderator = (await client.services.permissions.check(context.effective, BUILD_SUBMISSION_EDIT)).allowed

    async def authorize() -> bool:
        current = await access(build if prepared is None else prepared.build)
        return scope is not EditAccess.NONE and current in {scope, EditAccess.FULL}

    async def render_build(current: Build) -> Any:
        return await render_build_card(
            current,
            client.media_previews,
            schematic_summaries=client.services.schematic_summaries,
            published_media=client.services.published_build_media,
        )

    async def refresh_posts(build_id: int) -> None:
        await client.refresh_posts("build", str(build_id))

    forms = client.services.submission_forms
    manifest = await forms.edit_manifest(locale=None, context=context)
    category = build.category.value.lower()

    async def load_options(source: str) -> tuple[ChoiceOption, ...]:
        return (
            await forms.options(source, category, locale=None, context=ActingContext.for_actor(await actor()))
        ).options

    # Load every catalogue now: the restriction and pattern catalogues decide which of the build's
    # names are resolved, and the others label its saved answers in the overview.
    sources = sorted({field.option_source for field in manifest.fields_for(category) if field.option_source})
    workspace = BuildEditWorkspace(
        build,
        manifest,
        {source: await load_options(source) for source in sources},
        load_options=load_options,
        is_moderator=is_moderator,
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
        session_id=session,
    )
    return prepared


async def show_build_editor(request: sd.Request[Any, RedstoneSquid], screen: BuildEditScreen) -> BuildEditScreen | None:
    """Authorize and show a prepared editor under its user/build key."""
    if not await screen.may_edit():
        await request.respond(
            error_node(
                tr(t"bot-submission-ui-opening-cannot-edit-this-build = Cannot edit this build"),
                tr(
                    t"build-editor-edit-access-required = Only the "
                    t"build's submitter or a trusted staff member can edit it."
                ),
            ),
            audience="personal",
        )
        return None
    identity = (request.user.id, screen.build.id)
    key = sd.SessionKey.custom(
        "build-edit", identity if screen.session_id is None else (*identity, str(screen.session_id))
    )
    outcome = await request.respond(screen, session_key=key)
    return screen if isinstance(outcome, sd.Presented) else None


async def open_build_editor(
    request: sd.Request[Any, RedstoneSquid],
    build: Build,
    *,
    recovered: bool = False,
    session: UUID | None = None,
) -> BuildEditScreen | None:
    """Prepare and show a build editor in one call."""
    return await show_build_editor(
        request,
        await prepare_build_editor(request, build, recovered=recovered, session=session),
    )


__all__ = ["open_build_editor", "prepare_build_editor", "show_build_editor"]
