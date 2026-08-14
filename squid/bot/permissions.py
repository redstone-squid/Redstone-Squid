"""Discord commands for granting permissions and shaping roles.

`/perm explain` renders `Decision.trace` directly, so there is exactly one
implementation of the precedence rules: whatever the resolver did is what the
explanation says it did. The three effects are presented in Discord's own
vocabulary -- allow, deny, and "no rule" -- with `forbid` kept visually distinct
because it behaves differently from both.
"""

from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import info_layout, no_mentions
from squid.bot.utils.permissions import build_subject, requires, subject_for
from squid.core.errors import ValidationError
from squid.core.i18n import _
from squid.permissions.application.administration import (
    EXCLUDE_MODE,
    INCLUDE_MODE,
    Actor,
    PermissionAdministrationService,
    effect_label,
    scope_label,
)
from squid.permissions.domain import CATALOGUE, Effect, Reason, TraceStep
from squid.permissions.domain.catalogue import (
    PERM_AUDIT_VIEW,
    PERM_GRANT_GLOBAL,
    PERM_GRANT_GUILD,
    PERM_NODE_VIEW,
    PERM_SUBJECT_INSPECT,
    ROLE_DEFINITION_MANAGE,
    ROLE_DEFINITION_MANAGE_GUILD,
)

if TYPE_CHECKING:
    import squid.bot.app

PAGE_SIZE = 20
type ScopeChoice = Literal["guild", "global"]


class PermissionCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Permissions"):
    """Self-service permission administration."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.admin: PermissionAdministrationService = bot.services.permission_admin

    async def _actor(self, ctx: Context[BotT]) -> Actor:
        return await self.admin.actor(await subject_for(ctx))

    def _scope(self, ctx: Context[BotT], scope: ScopeChoice) -> int | None:
        """Turn the user's word into a stored scope.

        "guild" means this server; "global" means everywhere, and the service
        refuses it for anyone who is not a global granter.
        """
        if scope == "global":
            return None
        if ctx.guild is None:
            msg = "A guild-scoped grant has to be made inside the server it applies to."
            raise ValidationError(msg)
        return ctx.guild.id

    async def _account_id(self, user: discord.User | discord.Member) -> int:
        account = await self.bot.services.accounts.get_or_create_account(user.id)
        assert account.id is not None
        return account.id

    async def _reply(self, ctx: Context[BotT], title: str, body: str) -> None:
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=info_layout(t(locale, title), body or t(locale, _("Nothing to show."))),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    # ---- /perm ----------------------------------------------------------

    @hybrid_group(name="perm")
    @requires(PERM_NODE_VIEW, PERM_SUBJECT_INSPECT, PERM_GRANT_GUILD, PERM_AUDIT_VIEW, mode="any")
    async def perm_group(self, ctx: Context[BotT]) -> None:
        """Inspect and grant permissions."""
        await ctx.send_help("perm")

    @perm_group.command(name="nodes")
    @requires(PERM_NODE_VIEW)
    @app_commands.describe(search=app_commands.locale_str(_("Only show nodes containing this text.")))
    async def nodes(self, ctx: Context[BotT], search: str = "", page: int = 1) -> None:
        """List the permission nodes this bot defines."""
        matching = [node for node in CATALOGUE if search in node.name]
        pages = max(1, (len(matching) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(page, 1), pages)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        lines = [
            f"`{node.name}` ({node.scope.value}{', default allow' if node.default.value == 'allow' else ''})"
            f"{''.join(f' `@{tag.value}`' for tag in sorted(node.tags))}\n  {t(locale, node.description)}"
            for node in matching[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        ]
        await self._reply(ctx, _("Permission nodes ({page}/{pages})").format(page=page, pages=pages), "\n".join(lines))

    @nodes.autocomplete("search")
    async def _node_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _pattern_choices(current)

    @perm_group.command(name="grant")
    @requires(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL, mode="any")
    @app_commands.describe(
        pattern=app_commands.locale_str(_("A node, a wildcard like build.**, or a @tag selector.")),
        scope=app_commands.locale_str(_("Where the grant applies.")),
    )
    async def grant(
        self,
        ctx: Context[BotT],
        pattern: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "guild",
        *,
        reason: str = "",
    ) -> None:
        """Allow a user or Discord role to use a permission."""
        await self._write(ctx, user, role, pattern, scope, Effect.ALLOW, reason)

    @perm_group.command(name="deny")
    @requires(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL, mode="any")
    async def deny(
        self,
        ctx: Context[BotT],
        pattern: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "guild",
        *,
        reason: str = "",
    ) -> None:
        """Deny a permission. A more specific allow still wins; use forbid for an absolute stop."""
        await self._write(ctx, user, role, pattern, scope, Effect.DENY, reason)

    @perm_group.command(name="forbid")
    @requires(PERM_GRANT_GLOBAL)
    async def forbid(
        self,
        ctx: Context[BotT],
        pattern: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "global",
        *,
        reason: str,
    ) -> None:
        """Withhold a permission absolutely. Owner only, and the reason is recorded."""
        await self._write(ctx, user, role, pattern, scope, Effect.FORBID, reason)

    async def _write(
        self,
        ctx: Context[BotT],
        user: discord.User | None,
        role: discord.Role | None,
        pattern: str,
        scope: ScopeChoice,
        effect: Effect,
        reason: str,
    ) -> None:
        if (user is None) == (role is None):
            msg = "Name exactly one of user or role."
            raise ValidationError(msg)
        actor = await self._actor(ctx)
        await self.admin.grant(
            actor,
            pattern=pattern,
            effect=effect,
            account_id=await self._account_id(user) if user is not None else None,
            discord_role_id=role.id if role is not None else None,
            guild_id=ctx.guild.id if ctx.guild is not None else None,
            scope_guild_id=self._scope(ctx, scope),
            reason=reason or None,
        )
        subject = user.mention if user is not None else f"@{role.name}" if role is not None else "?"
        await self._reply(
            ctx,
            _("Permission updated"),
            f"{effect_label(int(effect))} `{pattern}` for {subject} ({scope_label(self._scope(ctx, scope))})",
        )

    @perm_group.command(name="revoke")
    @requires(PERM_GRANT_GUILD, PERM_GRANT_GLOBAL, mode="any")
    async def revoke(
        self,
        ctx: Context[BotT],
        pattern: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "guild",
    ) -> None:
        """Remove a rule entirely. Different from denying it: absence falls back to the default."""
        if (user is None) == (role is None):
            msg = "Name exactly one of user or role."
            raise ValidationError(msg)
        actor = await self._actor(ctx)
        removed = await self.admin.revoke(
            actor,
            pattern=pattern,
            account_id=await self._account_id(user) if user is not None else None,
            discord_role_id=role.id if role is not None else None,
            scope_guild_id=self._scope(ctx, scope),
        )
        body = _("Removed `{pattern}`.") if removed else _("No such rule; nothing changed.")
        await self._reply(ctx, _("Permission rule"), str(body).format(pattern=pattern))

    @perm_group.command(name="list")
    @requires(PERM_SUBJECT_INSPECT)
    async def list_rules(
        self, ctx: Context[BotT], user: discord.User | None = None, role: discord.Role | None = None
    ) -> None:
        """Show the rules and roles attached to a user or Discord role."""
        account_id = await self._account_id(user) if user is not None else None
        rules = await self.admin.rules_for(
            account_id=account_id,
            discord_role_id=role.id if role is not None else None,
            guild_id=ctx.guild.id if ctx.guild is not None else None,
        )
        assignments = await self.admin.assignments_for(
            account_id=account_id,
            discord_role_id=role.id if role is not None else None,
            guild_id=ctx.guild.id if ctx.guild is not None else None,
        )
        lines = [
            f"{effect_label(rule.effect)} `{rule.pattern}` ({scope_label(rule.scope_guild_id)})"
            + (f" — {rule.reason}" if rule.reason else "")
            for rule in rules
        ]
        lines += [
            f"role `{assignment.role_slug}` ({scope_label(assignment.scope_guild_id)})" for assignment in assignments
        ]
        await self._reply(ctx, _("Permission rules"), "\n".join(lines))

    @perm_group.command(name="explain")
    @requires(PERM_SUBJECT_INSPECT)
    async def explain(self, ctx: Context[BotT], user: discord.Member, node: str) -> None:
        """Show exactly why a user does or does not hold a permission."""
        subject = await build_subject(self.bot, user, ctx.guild.id if ctx.guild is not None else None)
        decision = await self.bot.services.permissions.check(subject, node)
        await self._reply(ctx, _("Permission decision"), render_decision(decision, user.display_name))

    @explain.autocomplete("node")
    @grant.autocomplete("pattern")
    @deny.autocomplete("pattern")
    @revoke.autocomplete("pattern")
    async def _pattern_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _pattern_choices(current)

    @perm_group.command(name="whoami")
    @requires(PERM_NODE_VIEW)
    async def whoami(self, ctx: Context[BotT]) -> None:
        """List the permissions you currently hold here."""
        held = await self.bot.services.permissions.capabilities(await subject_for(ctx), CATALOGUE)
        await self._reply(ctx, _("Your permissions"), "\n".join(f"`{name}`" for name in sorted(held)))

    @perm_group.command(name="test")
    @requires(PERM_SUBJECT_INSPECT)
    async def test(self, ctx: Context[BotT], user: discord.Member, node: str) -> None:
        """Answer one permission question with a plain yes or no."""
        subject = await build_subject(self.bot, user, ctx.guild.id if ctx.guild is not None else None)
        decision = await self.bot.services.permissions.check(subject, node)
        verdict = _("allowed") if decision.allowed else _("denied")
        await self._reply(ctx, _("Permission check"), f"`{node}` for {user.mention}: **{verdict}**")

    @perm_group.command(name="audit")
    @requires(PERM_AUDIT_VIEW)
    async def audit(self, ctx: Context[BotT], limit: int = 20) -> None:
        """Show recent permission changes."""
        rows = await self.admin.audit(
            guild_id=ctx.guild.id if ctx.guild is not None else None,
            limit=min(max(limit, 1), 50),
        )
        lines = [
            f"`{row.at}` **{row.action}**"
            + (f" `{row.pattern}`" if row.pattern else "")
            + (f" → <@{row.subject_id}>" if row.subject_kind == "account" and row.subject_id else "")
            + (f" — {row.reason}" if row.reason else "")
            for row in rows
        ]
        await self._reply(ctx, _("Permission audit"), "\n".join(lines))

    # ---- /role ----------------------------------------------------------

    @hybrid_group(name="role")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def role_group(self, ctx: Context[BotT]) -> None:
        """Create and compose permission roles."""
        await ctx.send_help("role")

    @role_group.command(name="list")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def list_roles(self, ctx: Context[BotT]) -> None:
        """List permission roles, highest rank first."""
        roles = await self.admin.roles(guild_id=ctx.guild.id if ctx.guild is not None else None)
        lines = [
            f"`{role.slug}` rank {role.rank}"
            + (" (built-in)" if role.protected else "")
            + (" (this server)" if role.guild_id is not None else "")
            for role in roles
        ]
        await self._reply(ctx, _("Permission roles"), "\n".join(lines))

    @role_group.command(name="show")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def show_role(self, ctx: Context[BotT], slug: str) -> None:
        """Show a role's patterns, composition, rank and the nodes it resolves to."""
        guild_id = ctx.guild.id if ctx.guild is not None else None
        role = await self.admin.role(slug, guild_id=guild_id)
        roles = await self.admin.roles(guild_id=guild_id)
        by_id = {other.id: other for other in roles}
        leaves = self.admin.resolved_leaves(role, roles)
        actor = await self._actor(ctx)
        beyond = self.admin.unmanageable_patterns(actor, role, roles)

        body = [
            f"**rank** {role.rank}" + (" (built-in)" if role.protected else ""),
            "**includes** " + (", ".join(f"`{item}`" for item in role.includes) or "—"),
            "**excludes** " + (", ".join(f"`{item}`" for item in role.excludes) or "—"),
            "**composes** "
            + (", ".join(f"`{by_id[edge].slug}`" for edge in role.includes_roles if edge in by_id) or "—"),
            f"**resolves to** {len(leaves)} nodes: " + ", ".join(f"`{leaf}`" for leaf in leaves[:15]),
        ]
        if beyond:
            # Surfaced up front, so the two gates disagreeing does not have to be
            # discovered one rejected edit at a time.
            body.append(
                f"⚠️ you can manage this role's rank but not {len(beyond)} of its permissions: "
                + ", ".join(f"`{item}`" for item in beyond[:5])
            )
        await self._reply(ctx, _("Role {slug}").format(slug=role.slug), "\n".join(body))

    @role_group.command(name="create")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def create_role(
        self, ctx: Context[BotT], slug: str, name: str, rank: int = 0, scope: ScopeChoice = "guild"
    ) -> None:
        """Create an empty role; add its permissions with /role include."""
        actor = await self._actor(ctx)
        role = await self.admin.create_role(
            actor,
            slug=slug,
            name=name,
            guild_id=self._scope(ctx, scope),
            rank=rank,
        )
        await self._reply(ctx, _("Role created"), f"`{role.slug}` at rank {role.rank}")

    @role_group.command(name="delete")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def delete_role(self, ctx: Context[BotT], slug: str) -> None:
        """Delete a role and every assignment of it."""
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        removed = await self.admin.delete_role(actor, await self.admin.role(slug, guild_id=guild_id))
        await self._reply(ctx, _("Role deleted") if removed else _("Role unchanged"), f"`{slug}`")

    @role_group.command(name="include")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def include(self, ctx: Context[BotT], slug: str, pattern: str) -> None:
        """Add a permission pattern to a role."""
        await self._pattern(ctx, slug, pattern, INCLUDE_MODE, _("Role includes `{pattern}`"))

    @role_group.command(name="exclude")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def exclude(self, ctx: Context[BotT], slug: str, pattern: str) -> None:
        """Subtract a pattern from a role. Not a deny: another role granting it still wins."""
        await self._pattern(ctx, slug, pattern, EXCLUDE_MODE, _("Role excludes `{pattern}`"))

    async def _pattern(self, ctx: Context[BotT], slug: str, pattern: str, mode: int, message: str) -> None:
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        role = await self.admin.role(slug, guild_id=guild_id)
        await self.admin.add_pattern(actor, role, pattern, mode=mode)
        await self._reply(ctx, _("Role updated"), str(message).format(pattern=pattern))

    @role_group.command(name="remove-pattern")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def remove_pattern(self, ctx: Context[BotT], slug: str, pattern: str) -> None:
        """Remove a pattern from a role, whether it was included or excluded."""
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        role = await self.admin.role(slug, guild_id=guild_id)
        removed = await self.admin.remove_pattern(actor, role, pattern)
        await self._reply(ctx, _("Role updated") if removed else _("Role unchanged"), f"`{pattern}`")

    @role_group.command(name="add-role")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def add_role(self, ctx: Context[BotT], slug: str, included: str) -> None:
        """Compose another role into this one; its edits then propagate immediately."""
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        await self.admin.add_include(
            actor,
            await self.admin.role(slug, guild_id=guild_id),
            await self.admin.role(included, guild_id=guild_id),
        )
        await self._reply(ctx, _("Role updated"), f"`{slug}` now includes `{included}`")

    @role_group.command(name="remove-role")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def remove_role(self, ctx: Context[BotT], slug: str, included: str) -> None:
        """Stop composing another role into this one."""
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        removed = await self.admin.remove_include(
            actor,
            await self.admin.role(slug, guild_id=guild_id),
            await self.admin.role(included, guild_id=guild_id),
        )
        await self._reply(ctx, _("Role updated") if removed else _("Role unchanged"), f"`{slug}` / `{included}`")

    @role_group.command(name="rank")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def rank(self, ctx: Context[BotT], slug: str, rank: int) -> None:
        """Set a role's management rank. Never affects who may do what."""
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        await self.admin.set_rank(actor, await self.admin.role(slug, guild_id=guild_id), rank)
        await self._reply(ctx, _("Role updated"), f"`{slug}` is now rank {rank}")

    @role_group.command(name="assign")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def assign(
        self,
        ctx: Context[BotT],
        slug: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "guild",
    ) -> None:
        """Give a permission role to a user or to everyone with a Discord role."""
        if (user is None) == (role is None):
            msg = "Name exactly one of user or role."
            raise ValidationError(msg)
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        await self.admin.assign(
            actor,
            await self.admin.role(slug, guild_id=guild_id),
            account_id=await self._account_id(user) if user is not None else None,
            discord_role_id=role.id if role is not None else None,
            guild_id=guild_id,
            scope_guild_id=self._scope(ctx, scope),
        )
        await self._reply(ctx, _("Role assigned"), f"`{slug}`")

    @role_group.command(name="unassign")
    @requires(ROLE_DEFINITION_MANAGE_GUILD, ROLE_DEFINITION_MANAGE, mode="any")
    async def unassign(
        self,
        ctx: Context[BotT],
        slug: str,
        user: discord.User | None = None,
        role: discord.Role | None = None,
        scope: ScopeChoice = "guild",
    ) -> None:
        """Take a permission role away."""
        if (user is None) == (role is None):
            msg = "Name exactly one of user or role."
            raise ValidationError(msg)
        actor = await self._actor(ctx)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        removed = await self.admin.unassign(
            actor,
            await self.admin.role(slug, guild_id=guild_id),
            account_id=await self._account_id(user) if user is not None else None,
            discord_role_id=role.id if role is not None else None,
            scope_guild_id=self._scope(ctx, scope),
        )
        await self._reply(ctx, _("Role unassigned") if removed else _("Nothing changed"), f"`{slug}`")


def _pattern_choices(current: str) -> list[app_commands.Choice[str]]:
    """Leaves, their wildcard ancestors, and tag selectors, filtered by what was typed."""
    candidates: list[str] = []
    for node in CATALOGUE:
        candidates.append(node.name)
        segments = node.name.split(".")
        candidates.extend(f"{'.'.join(segments[:depth])}.**" for depth in range(1, len(segments)))
        candidates.extend(f"@{tag.value}" for tag in node.tags)
    candidates.append("**")
    unique = sorted({candidate for candidate in candidates if current.lower() in candidate.lower()})
    return [app_commands.Choice(name=candidate, value=candidate) for candidate in unique[:25]]


def render_decision(decision, subject_label: str) -> str:
    """Render a decision the way the plan's worked example does, winner first."""
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


async def setup(bot: "squid.bot.app.RedstoneSquid") -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(PermissionCog(bot))
