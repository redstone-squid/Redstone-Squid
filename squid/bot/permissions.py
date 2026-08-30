"""Discord entry point for the canonical access-control workspace."""

from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from squid.bot.access_view import AccessScreen
from squid.bot.utils.accounts import account_id_for
from squid.bot.utils.permissions import allows, enforce, hide_unless, subject_for_interaction
from squid.core.errors import ValidationError
from squid.permissions.application.administration import PermissionAdministrationService, effect_label, scope_label
from squid.permissions.domain import Effect, PermissionNode, Reason, TraceStep
from squid.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)
from squid_ui_discord.ext import Cog

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


class PermissionCog[BotT: "squid.bot.app.RedstoneSquid"](Cog[BotT], name="Permissions"):
    """Open one capability-aware access workspace per administrator and guild."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self.admin: PermissionAdministrationService = bot.services.permission_admin

    @app_commands.command(name="access", description="Inspect and manage this server's access controls")
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def access(
        self,
        interaction: discord.Interaction[BotT],
        user: discord.Member | None = None,
        role: discord.Role | None = None,
    ) -> None:
        """Open subject rules, internal roles, assignments, catalogue, and audit."""
        if user is not None and role is not None:
            message = "Choose either a user or a Discord role, not both."
            raise ValidationError(message)
        await enforce(interaction, *ACCESS_NODES, mode="any")
        guild = interaction.guild
        assert guild is not None
        target = user or (None if role is not None else interaction.user)
        account_id = await account_id_for(self.bot.services.accounts, target) if target is not None else None
        role_id = role.id if role is not None else None
        label = role.name if role is not None else target.display_name if target is not None else "unknown"
        subject = await subject_for_interaction(interaction)
        capability_list: list[PermissionNode] = []
        for node in ACCESS_NODES:
            if await self.bot.services.permissions.allows(subject, node):
                capability_list.append(node)  # noqa: PERF401 - await comprehensions confuse Pyrefly.
        capabilities = frozenset(capability_list)

        async def actor():
            return await self.admin.actor(await subject_for_interaction(interaction))

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        await self.ui.respond(
            interaction,
            AccessScreen(
                self.admin,
                guild_id=guild.id,
                account_id=account_id,
                discord_role_id=role_id,
                subject_label=label,
                capabilities=capabilities,
                actor=actor,
                authorize=authorize,
            ),
        )


def render_decision(decision: Any, subject_label: str) -> str:
    """Render a permission decision with its winning rule first."""
    verdict = "ALLOWED" if decision.allowed else "DENIED"
    lines = [f"`{decision.node}` for {subject_label} → **{verdict}**", ""]
    for step in decision.trace:
        lines.append(_render_step(step))
        if step.rule.via is not None:
            lines.append(f"    via: {step.rule.via}")
    if decision.reason is Reason.DEFAULT:
        lines.append(f"no rule matched; catalogue default: {'allow' if decision.allowed else 'deny'}")
    elif decision.reason is Reason.OWNER:
        lines.append("bot owner: allowed before any rule is read")
    elif decision.reason is Reason.FORBIDDEN:
        lines.append("forbidden: absolute, and no allow can outrank it")
    return "\n".join(lines)


def _render_step(step: TraceStep) -> str:
    marker = "✗" if step.decisive and step.rule.effect is not Effect.ALLOW else ("✓" if step.decisive else "·")
    tail = "← decisive" if step.decisive else f"lost: {step.lost_on}"
    source = step.rule.source or "?"
    return (
        f"{marker} {effect_label(int(step.rule.effect))} `{step.rule.pattern.raw}` "
        f"{source} {scope_label(step.rule.scope_guild_id)} {tail}"
    )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the access-control cog."""
    await bot.add_cog(PermissionCog(bot))
