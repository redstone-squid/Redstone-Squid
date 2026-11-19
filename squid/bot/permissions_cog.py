"""Discord entry point for the canonical access-control workspace."""

from typing import TYPE_CHECKING, Self

import discord
from discord import app_commands

import squid_ui.ext.discord as sd
from squid.bot.access_view import AccessScreen
from squid.bot.utils.accounts import get_or_create_discord_account_id
from squid.bot.utils.permissions import Grants, enforce, hide_unless
from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.permissions.application.administration import PermissionAdministrationService
from squid.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid_i18n import tr

if TYPE_CHECKING:
    import squid.bot.app


ACCESS_NODES = (
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    PERM_GRANT_GUILD,
    PERM_GRANT_GLOBAL,
    PERM_AUDIT_VIEW,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)


class PermissionCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT], name="Permissions"):
    """Open one capability-aware access workspace per administrator and guild."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self.admin: PermissionAdministrationService = bot.services.permission_admin

    @sd.command(
        name=tr(t"command-access-name = access"),
        description=tr(t"command-access-help = Inspect and manage this server's access controls"),
        options={
            "user": sd.Option(
                name=tr(t"command-access-user-name = user"),
                description=tr(t"command-access-user-help = Start with this member selected."),
            ),
            "role": sd.Option(
                name=tr(t"command-access-role-name = role"),
                description=tr(
                    t"command-access-role-help = Discord role whose access to inspect. Choose either a member or a role."
                ),
            ),
        },
    )
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def access(
        self,
        request: sd.Request[Self, BotT],
        user: discord.Member | None = None,
        role: discord.Role | None = None,
    ) -> AccessScreen:
        """Open subject rules, internal roles, assignments, catalogue, and audit."""
        if user is not None and role is not None:
            message = "Choose either a user or a Discord role, not both."
            raise ValidationError(message)
        await enforce(request, *ACCESS_NODES, mode="any")
        guild = request.guild
        assert guild is not None
        target = user or (None if role is not None else request.user)
        account_id = (
            await get_or_create_discord_account_id(self.bot.services.accounts, target) if target is not None else None
        )
        role_id = role.id if role is not None else None
        label = role.name if role is not None else target.display_name if target is not None else "unknown"

        async def actor() -> Actor:
            return await request.client.actors.resolve(request, refresh=True)

        return AccessScreen(
            self.admin,
            self.bot.services.edits,
            guild_id=guild.id,
            guild_name=guild.name,
            target_chosen=user is not None or role is not None,
            bot=self.bot,
            member_id=target.id if target else None,
            account_id=account_id,
            discord_role_id=role_id,
            subject_label=label,
            grants=await Grants.load(request, *ACCESS_NODES),
            actor=actor,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the access-control cog."""
    await bot.add_cog(PermissionCog(bot))
