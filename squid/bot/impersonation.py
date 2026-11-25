"""Private operator commands for opening Discord workspaces under a debugging session."""

from typing import Self
from uuid import UUID

import discord

import squid_ui.ext.discord as sd
from squid.bot.app import RedstoneSquid
from squid.bot.i18n import to_locale_str
from squid.bot.submission.ui.controls import edit_build
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.ui import text_node
from squid.core.actors import ActingContext
from squid.core.errors import AuthorizationError, ValidationError
from squid_i18n import tr

_MEMBER = sd.Option(
    name=tr(t"impersonation-option-member = member"),
    description=tr(t"impersonation-option-member-help = Member whose Squid account to use"),
)
_REASON = sd.Option(
    name=tr(t"impersonation-option-reason = reason"),
    description=tr(t"impersonation-option-reason-help = Reason for debugging this account"),
)
_SESSION = sd.Option(
    name=tr(t"impersonation-option-session = session"),
    description=tr(t"impersonation-option-session-help = Debugging session UUID"),
)
_DRAFT = sd.Option(
    name=tr(t"impersonation-option-draft = draft"),
    description=tr(t"impersonation-option-draft-help = Submission draft UUID"),
)
_BUILD = sd.Option(
    name=tr(t"impersonation-option-build = build"), description=tr(t"impersonation-option-build-help = Build ID")
)


class ImpersonationCommands(sd.Cog[RedstoneSquid]):
    group = sd.Group(
        name=to_locale_str(tr(t"impersonation-group-name = impersonate")),
        description=to_locale_str(tr(t"impersonation-group-description = Manage account debugging sessions")),
    )

    @group.command(
        name=tr(t"impersonation-command-start = start"),
        description=tr(t"impersonation-command-start-help = Start a debugging session for a guild member"),
        defer="private",
        options={"member": _MEMBER, "reason": _REASON},
    )
    async def start(self, request: sd.Request[Self, RedstoneSquid], member: discord.Member, reason: str) -> None:
        if request.guild is None or member.guild.id != request.guild.id:
            raise AuthorizationError
        operator = await request.client.actors.resolve(request, refresh=True)
        target = await request.client.actors.resolve(member)
        session = await request.client.services.impersonation.start(
            f"discord:{request.guild.id}:{request.user.id}",
            target.require_account_id(),
            reason,
            context=ActingContext.for_actor(operator),
            target_discord_id=member.id,
        )
        session_id = session.id
        await request.respond(
            text_node(tr(t"impersonation-started = Debugging session {session_id!s} is open for 15 minutes.")),
            audience="personal",
        )

    @group.command(
        name=tr(t"impersonation-command-inspect = inspect"),
        description=tr(t"impersonation-command-inspect-help = Inspect a debugging session"),
        defer="private",
        options={"session": _SESSION},
    )
    async def inspect(self, request: sd.Request[Self, RedstoneSquid], session: str) -> None:
        if request.guild is None:
            raise AuthorizationError
        operator = await request.client.actors.resolve(request, refresh=True)
        record = await request.client.services.impersonation.inspect(
            f"discord:{request.guild.id}:{request.user.id}", _uuid(session), context=ActingContext.for_actor(operator)
        )
        target_id = record.target_id
        ends = (record.revoked_at or record.expires_at).format_iso()
        await request.respond(
            text_node(tr(t"impersonation-details = Target account {target_id}. Session end {ends}.")),
            audience="personal",
        )

    @group.command(
        name=tr(t"impersonation-command-stop = stop"),
        description=tr(t"impersonation-command-stop-help = End a debugging session"),
        defer="private",
        options={"session": _SESSION},
    )
    async def stop(self, request: sd.Request[Self, RedstoneSquid], session: str) -> None:
        if request.guild is None:
            raise AuthorizationError
        operator = await request.client.actors.resolve(request, refresh=True)
        await request.client.services.impersonation.stop(
            f"discord:{request.guild.id}:{request.user.id}", _uuid(session), context=ActingContext.for_actor(operator)
        )
        await request.respond(text_node(tr(t"impersonation-closed = Session closed.")), audience="personal")

    @group.command(
        name=tr(t"impersonation-command-draft = draft"),
        description=tr(t"impersonation-command-draft-help = Open a draft in a debugging session"),
        defer="private",
        options={"session": _SESSION, "draft": _DRAFT},
    )
    async def draft(self, request: sd.Request[Self, RedstoneSquid], session: str, draft: str) -> sd.CommandResult:
        return await DraftEditorScreen.open_draft(request, _uuid(draft), session=_uuid(session))

    @group.command(
        name=tr(t"impersonation-command-build = build"),
        description=tr(t"impersonation-command-build-help = Open a build editor in a debugging session"),
        defer="private",
        options={"session": _SESSION, "build": _BUILD},
    )
    async def build(self, request: sd.Request[Self, RedstoneSquid], session: str, build: int) -> None:
        await edit_build(request, build, session=_uuid(session))


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        msg = "Enter a valid UUID."
        raise ValidationError(msg) from None


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(ImpersonationCommands(bot))
