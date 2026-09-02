# type: ignore
"""Magical stuff, don't worry about it."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, Self, cast

import anyio
import discord
from discord import Interaction, app_commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.routes._root import _feature_group, _feature_route
from squid.bot.ui import render_payload, text_node, tr
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.community.domain import RedstonerDecisionKind
from squid.permissions.domain.catalogue import REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC
from squid_ui.text import localization_scope
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app


class _OwnerGuildClient(Protocol):
    owner_server_id: int


class OwnerGuildOnly[BotT: discord.Client](sd.routing.Middleware[BotT]):
    """Silently ignore durable role controls outside the configured owner guild."""

    async def dispatch(
        self,
        request: sd.routing.RouteRequest[BotT],
        proceed: sd.routing.RouteProceed,
    ) -> None:
        interaction = request.interaction
        client = cast(_OwnerGuildClient, interaction.client)
        if interaction.guild is None or interaction.guild.id != client.owner_server_id:
            return
        await proceed()


redstoner_roles, _new_redstoner_roles = _feature_group("redstoner-roles")
if _new_redstoner_roles:
    redstoner_roles.add_middleware(OwnerGuildOnly())
remove_redstoner_role = _feature_route(redstoner_roles, "self:remove", aliases=("remove:role:redstoner",))


@redstoner_roles.route(remove_redstoner_role)
async def remove_own_redstoner_role(interaction: Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """Let a member drop the redstoner role the bot gave them."""
    assert interaction.guild is not None
    member = interaction.user
    community = interaction.client.community_config
    redstoner_role = interaction.guild.get_role(community.redstoner_role_id)
    if redstoner_role is None or redstoner_role not in member.roles:
        return

    request = await sd.request(interaction)
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
    )(
        render_payload(
            [
                text_node(
                    tr(
                        "{owner}, {member} has removed their own redstoner role.",
                        owner=owner.mention,
                        member=member.mention,
                    )
                )
            ]
        )
    )
    await anyio.sleep(10)

    await member.add_roles(redstoner_role)
    await request.respond(
        text_node(tr("{member} — just kidding, here is your role back.", member=member.mention)),
        audience="personal",
    )


type RedstonerAuthorizer = Callable[[], Awaitable[bool]]
type PanelPublisher = Callable[[], Awaitable[None]]


class RedstonerScreen(sd.Screen):
    """A Redstoner deployment screen that ends when closed, replaced, or timed out."""

    session = sd.SessionSpec("redstoner", scope=sd.ScopeKind.USER_GUILD)
    timeout = 300
    audience = "personal"

    def __init__(
        self,
        *,
        guild_id: int,
        role_id: int,
        source_channel_id: int,
        can_deploy: bool,
        authorize_deploy: RedstonerAuthorizer,
        publish_panel: PanelPublisher,
    ) -> None:
        self._guild_id = guild_id
        self._role_id = role_id
        self._source_channel_id = source_channel_id
        self._can_deploy = can_deploy
        self._authorize_deploy = authorize_deploy
        self._publish_panel = publish_panel

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        role_mention = sl.raw_md(f"<@&{self._role_id}>")
        source_channel_mention = sl.raw_md(f"<#{self._source_channel_id}>")
        actions: list[sl.semantic.ActionControl] = []
        if self._can_deploy:
            actions.append(sl.action_control(tr(t"Deploy role controls"), self._deploy, key="deploy"))
        actions.append(sl.action_control(tr(t"Close"), self._close, key="close"))
        return (
            sl.section(
                sl.heading(tr(t"Redstoner automation")),
                sl.fields(
                    sl.field(tr(t"Role"), sl.md(tr(t"{role_mention}"))),
                    sl.field(tr(t"Source channel"), sl.md(tr(t"{source_channel_mention}"))),
                    sl.field(tr(t"Server"), str(self._guild_id)),
                ),
            ),
            sl.action_controls(*actions, key="redstoner-admin-actions"),
        )

    async def _deploy(self, event: sl.PressEvent) -> None:
        if not await self._authorize_deploy():
            await event.notice(tr(t"You are no longer allowed to deploy Redstoner controls."))
            return
        await self._publish_panel()
        await event.notice(tr(t"Redstoner role controls deployed."))

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


class GiveRedstoner[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.service = bot.services.redstoner

    @sd.Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
        await self.give_redstoner_from_message(message)

    @sd.command(name="redstoner", description="Inspect and deploy Redstoner automation")
    @app_commands.guild_only()
    @hide_unless(manage_roles=True)
    async def redstoner(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Open deployment status for the configured owner server."""
        await enforce(request, REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC, mode="any")
        guild = request.guild
        if guild is None or guild.id != self.bot.owner_server_id:
            return sd.Response(text_node(tr("This is only available in the bot's home server.")), audience="personal")

        async def may_deploy() -> bool:
            return await allows(request, REDSTONER_PANEL_MANAGE)

        async def publish_panel() -> None:
            channel = request.channel
            assert isinstance(channel, GuildMessageable)
            await send_to(channel)(
                render_payload(
                    [
                        # One persistent panel is shared by the whole channel, so a
                        # requester's locale would be misleading for everyone else.
                        sl.primitives.Text("Redstoner role controls"),
                        sl.action_controls(
                            sl.routed_action_control(
                                "I'm not a redstoner",
                                remove_redstoner_role.id(),
                                key="remove-redstoner",
                                tone=sl.Tone.DANGER,
                            ),
                            key="redstoner-actions",
                        ),
                    ]
                )
            )

        community = self.bot.community_config
        return RedstonerScreen(
            guild_id=guild.id,
            role_id=community.redstoner_role_id,
            source_channel_id=community.redstoner_corner_channel_id,
            can_deploy=await may_deploy(),
            authorize_deploy=may_deploy,
            publish_panel=publish_panel,
        )

    @sd.context_menu(
        name="Resync Redstoner",
        defer="private",
        default_permissions=discord.Permissions(manage_roles=True),
    )
    async def resync_redstoner_context(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Reprocess the selected message for Redstoner role automation."""
        await enforce(request, REDSTONER_ROLE_RESYNC)
        guild = request.guild
        if guild is None or guild.id != self.bot.owner_server_id or message.guild != guild:
            return text_node(tr("That message is not in the bot's home server."))
        await self.give_redstoner_from_message(message)
        return text_node(tr("Redstoner automation resynced."))

    async def give_redstoner_from_message(self, message: discord.Message) -> None:
        """Give the redstoner role to a user based on a Starboard message."""
        decision = self.service.evaluate(
            author_id=message.author.id,
            channel_id=message.channel.id,
            mentioned_user_ids=[mention.id for mention in message.mentions],
            content=message.content,
        )
        if decision.kind is RedstonerDecisionKind.IGNORE:
            return

        locale = await resolve_locale(message, self.bot.services.settings)
        if decision.kind is RedstonerDecisionKind.MALFORMED:
            with localization_scope(localization_for(locale)):
                payload = render_payload(
                    [text_node(tr("{reason} in {url}", reason=decision.reason, url=message.jump_url))]
                )
            await send_to(message.channel)(payload)
            return

        assert decision.member_id is not None
        assert decision.source_message_url is not None
        member = next(mention for mention in message.mentions if mention.id == decision.member_id)
        assert message.guild is not None
        redstoner_role = message.guild.get_role(self.bot.community_config.redstoner_role_id)
        if redstoner_role is None:
            with localization_scope(localization_for(locale)):
                payload = render_payload([text_node(tr("Could not find the redstoner role."))])
            await send_to(message.channel)(payload)
            return
        await member.add_roles(redstoner_role)
        with localization_scope(localization_for(locale)):
            payload = render_payload([text_node(tr("Gave {member} the redstoner role.", member=member.mention))])
        await send_to(message.channel)(payload)

        with localization_scope(localization_for(locale)):
            presentation = render_payload(
                [
                    sl.primitives.Text(
                        tr(
                            "Hi {member}, you received the {role} role after reaching 15 upvotes in {url}.",
                            member=member.mention,
                            role=redstoner_role.mention,
                            url=decision.source_message_url,
                        )
                    )
                ],
                locale=locale,
            )
        await send_to(
            self.bot.get_channel(self.bot.community_config.redstoner_announcement_channel_id),
            allowed_mentions=discord.AllowedMentions(roles=False, users=(member,), everyone=False),
        )(presentation)


async def setup(bot: squid.bot.app.RedstoneSquid):
    await bot.add_cog(GiveRedstoner(bot))
