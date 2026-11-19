# type: ignore
"""Award the configured Redstoner role from community messages and expose role controls."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Self

import anyio
import discord
from discord import app_commands

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.routes import AppRequest, router
from squid.bot.ui import render_payload, text_node, tr
from squid.bot.utils.permissions import Grants, enforce, hide_unless, requires_grant
from squid.core.community.application import RedstonerService
from squid.core.community.domain import RedstonerDecisionKind, RedstonerPolicy
from squid.core.permissions.domain.catalogue import REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC
from squid_i18n import md_tr
from squid_ui.content.text import localization_scope
from squid_ui.ext.discord import send_to
from squid_ui.ext.discord.i18n import to_locale_str
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.routing import Proceed, RouteMatch

if TYPE_CHECKING:
    import squid.bot.app


async def owner_guild_only(request: AppRequest, match: RouteMatch[AppRequest], proceed: Proceed) -> Any:
    if request.interaction is None or request.interaction.guild is None:
        return None
    if request.interaction.guild.id != request.client.owner_server_id:
        return None
    return await proceed()


redstoner_roles = router.group("/redstoner-roles")
redstoner_roles.add_middleware(owner_guild_only, key="owner-guild")


@redstoner_roles.route("/self/remove")
async def remove_own_redstoner_role(request: AppRequest) -> None:
    """Temporarily remove the member's Redstoner role, announce it, and restore it after ten seconds.

    The route middleware limits this action to the owner guild. Removal and
    restoration are separate Discord calls; interruption can leave the role removed.
    """
    interaction = request.interaction
    assert interaction is not None and interaction.guild is not None
    member = interaction.user
    community = await interaction.client.services.settings.get_runtime()
    redstoner_role = interaction.guild.get_role(community.redstoner_role_id)
    if redstoner_role is None or redstoner_role not in member.roles:
        return

    await member.remove_roles(redstoner_role)
    owner = interaction.client.get_user(interaction.client.owner_id)
    assert owner is not None
    redstoner_channel = interaction.client.get_channel(community.redstoner_corner_channel_id)
    assert isinstance(redstoner_channel, GuildMessageable)
    await send_to(
        redstoner_channel,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            users=(owner, member),
            roles=False,
            replied_user=False,
        ),
    )(render_payload([text_node(_removed_role_notice(owner.id, member.id))]))
    await anyio.sleep(10)

    await member.add_roles(redstoner_role)
    await request.respond(
        text_node(_restored_role_notice(member.id)),
        audience="personal",
    )


type PanelPublisher = Callable[[], Awaitable[None]]


class RedstonerScreen(sd.Screen):
    """A Redstoner deployment screen that ends when replaced or timed out."""

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("redstoner", scope=sd.ScopeKind.USER_GUILD))

    def __init__(
        self,
        *,
        guild_id: int,
        role_id: int,
        source_channel_id: int,
        grants: Grants,
        publish_panel: PanelPublisher,
    ) -> None:
        self._guild_id = guild_id
        self._role_id = role_id
        self._source_channel_id = source_channel_id
        self.grants = grants
        self._publish_panel = publish_panel

    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        role_mention = sl.mention(self._role_id, kind="role")
        source_channel_mention = sl.mention(self._source_channel_id, kind="channel")
        actions: list[sl.semantic.ActionControl] = []
        if REDSTONER_PANEL_MANAGE in self.grants:
            actions.append(
                sl.action_control(
                    tr(t"give-redstoner-deploy-role-controls = Deploy role controls"), self._deploy, key="deploy"
                )
            )
        return (
            sl.section(
                sl.heading(tr(t"give-redstoner-redstoner-automation = Redstoner automation")),
                sl.fields(
                    sl.field(
                        tr(t"give-redstoner-role = Role"),
                        md_tr(t"give-redstoner-value = {role_mention}"),
                    ),
                    sl.field(
                        tr(t"give-redstoner-source-channel = Source channel"),
                        md_tr(t"give-redstoner-value-2 = {source_channel_mention}"),
                    ),
                    sl.field(tr(t"give-redstoner-server = Server"), str(self._guild_id)),
                ),
            ),
            sl.action_controls(*actions, key="redstoner-admin-actions"),
        )

    @requires_grant(
        REDSTONER_PANEL_MANAGE,
        denied=tr(t"redstoner-deploy-forbidden = You are no longer allowed to deploy Redstoner controls."),
    )
    async def _deploy(self, event: sl.PressEvent) -> None:
        await self._publish_panel()
        await event.notice(tr(t"give-redstoner-redstoner-role-controls = Redstoner role controls deployed."))


class GiveRedstoner[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    def __init__(self, bot: BotT):
        super().__init__(bot)

    @sd.Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
        await self.give_redstoner_from_message(message)

    @sd.command(
        name=to_locale_str(tr(t"command-redstoner-name = redstoner")),
        description=to_locale_str(tr(t"command-redstoner-help = Inspect and deploy Redstoner automation")),
    )
    @app_commands.guild_only()
    @hide_unless(manage_roles=True)
    async def redstoner(self, request: sd.Request[Self, BotT]) -> sd.CommandResult:
        """Open deployment status for the configured owner server."""
        await enforce(request, REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC, mode="any")
        guild = request.guild
        if guild is None or guild.id != self.bot.owner_server_id:
            return sd.Response(
                text_node(tr(t"redstoner-control-bot-only = This is only available in the bot's home server.")),
                audience="personal",
            )

        async def publish_panel() -> None:
            channel = request.channel
            assert isinstance(channel, GuildMessageable)
            await send_to(channel)(
                render_payload(
                    [
                        # One persistent panel is shared by the whole channel, so a
                        # requester's locale would be misleading for everyone else.
                        sd.v2.text("Redstoner role controls"),
                        sl.action_controls(
                            sl.routed_action_control(
                                "I'm not a redstoner",
                                remove_own_redstoner_role.custom_id(),
                                key="remove-redstoner",
                                tone=sl.Tone.DANGER,
                            ),
                            key="redstoner-actions",
                        ),
                    ]
                )
            )

        community = await self.bot.services.settings.get_runtime()
        return RedstonerScreen(
            guild_id=guild.id,
            role_id=community.redstoner_role_id,
            source_channel_id=community.redstoner_corner_channel_id,
            grants=await Grants.load(request, REDSTONER_PANEL_MANAGE),
            publish_panel=publish_panel,
        )

    async def give_redstoner_from_message(self, message: discord.Message) -> None:
        """Give the redstoner role to a user based on a Starboard message."""
        if message.guild is None or message.guild.id != self.bot.owner_server_id:
            return
        community = await self.bot.services.settings.get_runtime()
        if not community.redstoner_enabled:
            return
        service = RedstonerService(
            RedstonerPolicy(
                community.redstoner_starboard_author_id,
                community.redstoner_starboard_channel_id,
            )
        )
        decision = service.evaluate(
            author_id=message.author.id,
            channel_id=message.channel.id,
            mentioned_user_ids=[mention.id for mention in message.mentions],
            content=message.content,
        )
        if decision.kind is RedstonerDecisionKind.IGNORE:
            return

        locale = await resolve_locale(message, self.bot.services.settings)
        if decision.kind is RedstonerDecisionKind.MALFORMED:
            reason = sl.plain(decision.reason)
            url = sl.text_link(message.jump_url, message.jump_url)
            with localization_scope(localization_for(locale)):
                payload = render_payload([text_node(md_tr(t"give-redstoner-in = {reason} in {url}"))])
            await send_to(message.channel)(payload)
            return

        assert decision.member_id is not None
        assert decision.source_message_url is not None
        member = next(mention for mention in message.mentions if mention.id == decision.member_id)
        assert message.guild is not None
        redstoner_role = message.guild.get_role(community.redstoner_role_id)
        if redstoner_role is None:
            with localization_scope(localization_for(locale)):
                payload = render_payload(
                    [text_node(tr(t"give-redstoner-could-not-find-the-redstoner = Could not find the redstoner role."))]
                )
            await send_to(message.channel)(payload)
            return
        await member.add_roles(redstoner_role)
        with localization_scope(localization_for(locale)):
            payload = render_payload([text_node(_role_granted_notice(member.id))])
        await send_to(message.channel)(payload)

        with localization_scope(localization_for(locale)):
            url = decision.source_message_url
            presentation = render_payload(
                [sd.v2.text(_role_award_notice(member.id, redstoner_role.id, url))],
                locale=locale,
            )
        await send_to(
            self.bot.get_channel(community.redstoner_announcement_channel_id),
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False),
        )(presentation)


def _removed_role_notice(owner_id: int, member_id: int) -> sl.TextLike:
    owner = sl.mention(owner_id)
    member = sl.mention(member_id)
    return md_tr(t"redstoner-role-self-removed = {owner}, {member} has removed their own redstoner role.")


def _restored_role_notice(member_id: int) -> sl.TextLike:
    member = sl.mention(member_id)
    return md_tr(t"redstoner-role-restored = {member} — just kidding, here is your role back.")


def _role_granted_notice(member_id: int) -> sl.TextLike:
    member = sl.mention(member_id)
    return md_tr(t"give-redstoner-gave-the-redstoner-role = Gave {member} the redstoner role.")


def _role_award_notice(member_id: int, role_id: int, destination: str) -> sl.TextLike:
    member = sl.mention(member_id)
    role = sl.mention(role_id, kind="role")
    url = sl.text_link(destination, destination)
    return md_tr(
        t"redstoner-role-earned = Hi {member}, you received the {role} role after reaching 15 upvotes in {url}.",
    )


async def setup(bot: squid.bot.app.RedstoneSquid):
    await bot.add_cog(GiveRedstoner(bot))
