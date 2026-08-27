# type: ignore
"""Magical stuff, don't worry about it."""

import asyncio
from typing import TYPE_CHECKING, Protocol, cast

import discord
from discord import Interaction
from discord.ext.commands import Cog, Context, hybrid_group

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale, t
from squid.bot.routes import routes
from squid.bot.ui import render_payload, reply_payload, respond_payload, text_layout
from squid.bot.utils.permissions import check_is_home_server, hide_unless, requires
from squid.community.domain import RedstonerDecisionKind
from squid.core.i18n import _
from squid.permissions.domain.catalogue import REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC
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


redstoner_roles = routes.group("redstoner-roles")
redstoner_roles.add_middleware(OwnerGuildOnly())
remove_redstoner_role = redstoner_roles.define("self:remove", aliases=("remove:role:redstoner",))


@redstoner_roles.route(remove_redstoner_role)
async def remove_own_redstoner_role(interaction: Interaction[squid.bot.app.RedstoneSquid]) -> None:
    """Let a member drop the redstoner role the bot gave them."""
    assert interaction.guild is not None
    member = interaction.user
    community = interaction.client.community_config
    redstoner_role = interaction.guild.get_role(community.redstoner_role_id)
    if redstoner_role is None or redstoner_role not in member.roles:
        return

    locale = await resolve_locale(interaction, interaction.client.services.settings)
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
        text_layout(
            t(
                locale,
                _("{owner}, {member} has removed their own redstoner role."),
                owner=owner.mention,
                member=member.mention,
            )
        )
    )
    await asyncio.sleep(10)

    await member.add_roles(redstoner_role)
    await respond_payload(
        interaction,
        text_layout(t(locale, _("{member} — just kidding, here is your role back."), member=member.mention)),
    )


class GiveRedstoner[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.service = bot.services.redstoner

    @Cog.listener("on_message")
    async def give_redstoner(self, message: discord.Message):
        await self.give_redstoner_from_message(message)

    @hybrid_group(name="redstoner")
    @check_is_home_server()
    @requires(REDSTONER_PANEL_MANAGE, REDSTONER_ROLE_RESYNC, mode="any")
    @hide_unless(manage_roles=True)
    async def redstoner_group(self, ctx: Context[BotT]) -> None:
        """Manage Redstoner role automation."""
        await ctx.send_help("redstoner")

    @redstoner_group.command(name="panel")
    @check_is_home_server()
    @requires(REDSTONER_PANEL_MANAGE)
    async def abc(self, ctx: Context[BotT]):
        """Post the Redstoner role controls."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        presentation = render_payload(
            [
                sl.primitives.Text(t(locale, _("Redstoner role controls"))),
                # Not translated: one panel is read by everyone in the channel, so the
                # guild's locale would still be the wrong language for most of them.
                sl.action_controls(
                    sl.routed_action_control(
                        "I'm not a redstoner",
                        remove_redstoner_role.id(),
                        key="remove-redstoner",
                        tone=sl.Tone.DANGER,
                    ),
                    key="redstoner-actions",
                ),
            ],
            locale=locale,
        )
        await reply_payload(ctx, presentation)

    @redstoner_group.command(name="resync")
    @check_is_home_server()
    @requires(REDSTONER_ROLE_RESYNC)
    async def force_reload_message(self, ctx: Context[BotT], message: discord.Message):
        """Reprocess a message for Redstoner role automation."""
        await self.give_redstoner_from_message(message)

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
            await send_to(message.channel)(
                text_layout(t(locale, _("{reason} in {url}"), reason=decision.reason, url=message.jump_url))
            )
            return

        assert decision.member_id is not None
        assert decision.source_message_url is not None
        member = next(mention for mention in message.mentions if mention.id == decision.member_id)
        assert message.guild is not None
        redstoner_role = message.guild.get_role(self.bot.community_config.redstoner_role_id)
        if redstoner_role is None:
            await send_to(message.channel)(text_layout(t(locale, _("Could not find the redstoner role."))))
            return
        await member.add_roles(redstoner_role)
        await send_to(message.channel)(
            text_layout(t(locale, _("Gave {member} the redstoner role."), member=member.mention))
        )

        presentation = render_payload(
            [
                sl.primitives.Text(
                    t(
                        locale,
                        _("Hi {member}, you received the {role} role after reaching 15 upvotes in {url}."),
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
