"""Discord transport boundary for opening build-edit screens."""

from typing import TYPE_CHECKING, Any

import discord

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
    interaction: discord.Interaction[RedstoneSquid],
    build: Build,
    builds: BuildService | None = None,
) -> BuildEditScreen:
    """Inject actor-aware Discord operations into one build editor."""
    client = interaction.client
    prepared: BuildEditScreen | None = None

    async def authorize() -> bool:
        current = build if prepared is None else prepared.build
        actor_account_id = await client.account_ids.resolve(client.services.accounts, interaction.user.id)
        if (
            current.submission_status is Status.PENDING
            and actor_account_id is not None
            and current.submitter_account_id == actor_account_id
        ):
            return True
        return await allows(interaction, BUILD_SUBMISSION_EDIT)

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
    )
    return prepared


async def show_build_editor(
    interaction: discord.Interaction[RedstoneSquid],
    screen: BuildEditScreen,
) -> BuildEditScreen | None:
    """Authorize and show a prepared editor under its user/build key."""
    if not await screen.may_edit():
        invocation = await sd.Invocation.of(interaction)
        await invocation.reply(
            error_node(
                tr(t"Cannot edit this build"),
                tr(t"Only the pending build's submitter or a trusted staff member can edit it."),
            ),
            visibility="personal",
        )
        return None
    key = sd.SessionKey.custom("build-edit", (interaction.user.id, screen.build.id))
    return await screen.show(interaction, key=key, wait=True)


async def open_build_editor(
    interaction: discord.Interaction[RedstoneSquid],
    build: Build,
) -> BuildEditScreen | None:
    """Prepare and show a build editor in one call."""
    return await show_build_editor(interaction, await prepare_build_editor(interaction, build))


__all__ = ["open_build_editor", "prepare_build_editor", "show_build_editor"]
