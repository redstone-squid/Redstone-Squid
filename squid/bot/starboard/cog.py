"""Discord listeners and configuration commands for starboards."""

import asyncio
import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, guild_only, hybrid_group
from whenever import Instant

from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale, t
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.bot.starboard.render import starboard_layout
from squid.bot.utils.components import edit_layout, no_mentions, text_layout
from squid.bot.utils.permissions import check_is_server_admin
from squid.core.i18n import _
from squid.reactions.domain import ReactionActor
from squid.starboard.application import EntryPlan
from squid.starboard.domain import EntryAction, OriginMessage, StarboardConfig, StarboardEmoji

if TYPE_CHECKING:
    import squid.bot.app


class StarboardCog[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Mirror messages after their weighted reactions cross configured thresholds."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.service = bot.services.starboards
        self._debouncer = EntryDebouncer(self._refresh_key, supervisor=bot.background_tasks)
        self.bot.reactions.subscribe(self)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        await self._debouncer.drain()

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        payload = event.payload
        if payload.guild_id is None or self.bot.user is None or payload.user_id == self.bot.user.id:
            return
        if not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        message = await event.message()
        member = await event.resolve_member()
        if message is None or member is None or member.bot or message.author.id == self.bot.user.id:
            return
        origin = self._origin(message)
        result = await self.service.record_vote(
            origin,
            ReactionActor(member.id, member.guild.id, frozenset(role.id for role in member.roles)),
            event.emoji,
        )
        if result.remove_reaction:
            with contextlib.suppress(discord.NotFound, discord.Forbidden):
                await message.remove_reaction(payload.emoji, member)
        self._schedule(result.plans)

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        payload = event.payload
        if payload.guild_id is None or not await self.service.is_relevant_emoji(payload.guild_id, event.emoji):
            return
        self._schedule(await self.service.withdraw_vote(payload.message_id, payload.user_id, event.emoji))

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        self._schedule(await self.service.clear_votes(event.payload.message_id))

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        self._schedule(await self.service.clear_votes(event.payload.message_id, event.emoji))

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        plans = await self.service.refresh(payload.message_id, force=True)
        self._schedule((plan for plan in plans if plan.config.link_edits), force=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        deleted_post = await self.service.reset_deleted_post(payload.message_id)
        if deleted_post is not None:
            self._debouncer.schedule(deleted_post, force=True)
        self._schedule(await self.service.mark_origin_deleted(payload.message_id), force=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.service.disable_channel(channel.id)

    def _schedule(self, plans: Iterable[EntryPlan], *, force: bool = False) -> None:
        for plan in plans:
            self._debouncer.schedule((plan.config.id, plan.origin.id), force=force)

    async def _refresh_key(self, key: EntryKey, force: bool) -> None:
        starboard_id, origin_message_id = key
        plans = await self.service.refresh(origin_message_id, force=force)
        plan = next((item for item in plans if item.config.id == starboard_id), None)
        if plan is not None:
            await self._execute(plan)

    async def _execute(self, plan: EntryPlan) -> None:
        if plan.action is EntryAction.NOOP:
            return
        if plan.action is EntryAction.REMOVE:
            if plan.entry.posted_message_id is not None and plan.entry.posted_channel_id is not None:
                posted = await self._message(plan.entry.posted_channel_id, plan.entry.posted_message_id)
                if posted is not None:
                    with contextlib.suppress(discord.NotFound):
                        await posted.delete()
            await self.service.mark_removed(plan)
            return
        destination = await self._channel(plan.config.channel_id)
        origin = await self._message(plan.origin.channel_id, plan.origin.id)
        if destination is None or origin is None or self._unsafe_nsfw(origin.channel, destination):
            return
        mentions = (
            discord.AllowedMentions(everyone=False, roles=False, users=(origin.author,), replied_user=False)
            if plan.config.ping_author
            else no_mentions()
        )
        locale = await resolve_locale(origin, self.bot.services.settings)
        if plan.action is EntryAction.SEND:
            posted = await destination.send(
                view=starboard_layout(plan, origin, locale=locale), allowed_mentions=mentions
            )
            await self.service.mark_posted(plan, posted.id, destination.id)
            await self._autoreact(posted, plan)
            return
        if plan.entry.posted_message_id is None or plan.entry.posted_channel_id is None:
            return
        posted = await self._message(plan.entry.posted_channel_id, plan.entry.posted_message_id)
        if posted is None:
            await self.service.mark_removed(plan)
            self._debouncer.schedule((plan.config.id, plan.origin.id), force=True)
            return
        await edit_layout(posted, starboard_layout(plan, origin, locale=locale), allowed_mentions=mentions)
        await self.service.mark_rendered(plan)

    async def _autoreact(self, message: discord.Message, plan: EntryPlan) -> None:
        for item in plan.config.emojis:
            enabled = plan.config.autoreact_upvote if item.direction == "up" else plan.config.autoreact_downvote
            if enabled:
                with contextlib.suppress(discord.Forbidden):
                    await message.add_reaction(item.emoji)
                await asyncio.sleep(0)

    async def _channel(self, channel_id: int) -> GuildMessageable | None:
        channel = await self.bot.get_or_fetch_messageable_channel(channel_id)
        return channel if isinstance(channel, GuildMessageable) else None

    async def _message(self, channel_id: int, message_id: int) -> discord.Message | None:
        return await self.bot.get_or_fetch_message(channel_id, message_id, untrack_if_missing=False)

    @staticmethod
    def _unsafe_nsfw(source: discord.abc.Messageable, destination: GuildMessageable) -> bool:
        source_nsfw = bool(getattr(source, "is_nsfw", lambda: False)())
        destination_nsfw = bool(getattr(destination, "is_nsfw", lambda: False)())
        return source_nsfw and not destination_nsfw

    @staticmethod
    def _origin(message: discord.Message) -> OriginMessage:
        assert message.guild is not None
        channel_nsfw = bool(getattr(message.channel, "is_nsfw", lambda: False)())
        has_image = any((item.content_type or "").startswith("image/") for item in message.attachments) or any(
            embed.image.url or embed.thumbnail.url for embed in message.embeds
        )
        return OriginMessage(
            message.id,
            message.guild.id,
            message.channel.id,
            message.author.id,
            message.author.bot,
            Instant(message.created_at),
            is_nsfw=channel_nsfw,
            has_image=has_image,
        )

    @hybrid_group(name="starboard")
    @guild_only()
    @check_is_server_admin()
    async def starboard_group(self, ctx: Context[BotT]) -> None:
        """Configure weighted message starboards."""
        await ctx.send_help("starboard")

    @starboard_group.command(name="create")
    @app_commands.describe(channel=app_commands.locale_str(_("The channel that receives starred messages.")))
    async def create_starboard(self, ctx: Context[BotT], channel: discord.TextChannel, name: str = "main") -> None:
        assert ctx.guild is not None
        permissions = channel.permissions_for(ctx.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
            await self._reply(ctx, _("I need View Channel, Send Messages, and Read Message History there."))
            return
        config = await self.service.create_starboard(ctx.guild.id, channel.id, name)
        await self._reply(
            ctx, _("Created starboard **{name}** in {channel}."), name=config.name, channel=channel.mention
        )

    @starboard_group.command(name="delete")
    async def delete_starboard(self, ctx: Context[BotT], name: str) -> None:
        assert ctx.guild is not None
        deleted = await self.service.delete_starboard(ctx.guild.id, name)
        await self._reply(ctx, _("Starboard deleted.") if deleted else _("No starboard with that name exists."))

    @starboard_group.command(name="list")
    async def list_starboards(self, ctx: Context[BotT]) -> None:
        assert ctx.guild is not None
        configs = await self.service.list_for_guild(ctx.guild.id)
        lines = [f"**{item.name}** · <#{item.channel_id}> · {item.required:g}" for item in configs]
        await self._reply(ctx, "\n".join(lines) or _("No starboards are configured."))

    @starboard_group.command(name="show")
    async def show_starboard(self, ctx: Context[BotT], name: str) -> None:
        assert ctx.guild is not None
        config = await self.service.get(ctx.guild.id, name)
        if config is None:
            await self._reply(ctx, _("No starboard with that name exists."))
            return
        emojis = " ".join(f"{item.emoji} ({item.direction}, {item.multiplier:g}x)" for item in config.emojis)
        await self._reply(
            ctx,
            _("**{name}** · <#{channel}>\nPost: {required:g} · Remove: {remove:g}\nEmojis: {emojis}"),
            name=config.name,
            channel=config.channel_id,
            required=config.required,
            remove=config.required_remove,
            emojis=emojis,
        )

    @starboard_group.command(name="edit")
    async def edit_starboard(self, ctx: Context[BotT], name: str, setting: str, value: str) -> None:
        assert ctx.guild is not None
        try:
            parsed = self._parse_setting(setting, value)
        except ValueError as error:
            await self._reply(ctx, str(error))
            return
        config = await self.service.update_settings(ctx.guild.id, name, **{setting: parsed})
        await self._reply(ctx, _("Starboard updated.") if config else _("No starboard with that name exists."))

    @starboard_group.group(name="emoji")
    async def emoji_group(self, ctx: Context[BotT]) -> None:
        """Configure starboard reaction aliases."""
        await ctx.send_help("starboard emoji")

    @emoji_group.command(name="add")
    async def emoji_add(
        self,
        ctx: Context[BotT],
        name: str,
        emoji: str,
        direction: Literal["up", "down"] = "up",
        multiplier: float = 1.0,
    ) -> None:
        config = await self._named(ctx, name)
        if config is None:
            return
        aliases = (
            *(item for item in config.emojis if item.emoji != emoji),
            StarboardEmoji(emoji, direction, multiplier, len(config.emojis)),
        )
        await self.service.set_emojis(config, aliases)
        await self._reply(ctx, _("Starboard emoji added."))

    @emoji_group.command(name="remove")
    async def emoji_remove(self, ctx: Context[BotT], name: str, emoji: str) -> None:
        config = await self._named(ctx, name)
        if config is None:
            return
        await self.service.set_emojis(config, tuple(item for item in config.emojis if item.emoji != emoji))
        await self._reply(ctx, _("Starboard emoji removed."))

    @emoji_group.command(name="list")
    async def emoji_list(self, ctx: Context[BotT], name: str) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self._reply(
                ctx, "\n".join(f"{item.emoji}: {item.direction} {item.multiplier:g}x" for item in config.emojis)
            )

    @starboard_group.group(name="weight")
    async def weight_group(self, ctx: Context[BotT]) -> None:
        """Configure role multipliers."""
        await ctx.send_help("starboard weight")

    @weight_group.command(name="set")
    async def weight_set(self, ctx: Context[BotT], name: str, role: discord.Role, multiplier: float) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self.service.set_role_multiplier(config, role.id, multiplier)
            await self._reply(ctx, _("Starboard role weight updated."))

    @weight_group.command(name="remove")
    async def weight_remove(self, ctx: Context[BotT], name: str, role: discord.Role) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self.service.set_role_multiplier(config, role.id, None)
            await self._reply(ctx, _("Starboard role weight removed."))

    @weight_group.command(name="list")
    async def weight_list(self, ctx: Context[BotT], name: str) -> None:
        config = await self._named(ctx, name)
        if config is None:
            return
        values = await self.service.get_role_multipliers(config)
        await self._reply(
            ctx, "\n".join(f"<@&{role_id}>: {value:g}x" for role_id, value in values.items()) or _("None")
        )

    @starboard_group.command(name="recount")
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def recount(self, ctx: Context[BotT], message: discord.Message) -> None:
        reactions: list[tuple[ReactionActor, str]] = []
        for reaction in message.reactions:
            reactions.extend(
                [
                    (
                        ReactionActor(user.id, user.guild.id, frozenset(role.id for role in user.roles)),
                        str(reaction.emoji),
                    )
                    async for user in reaction.users()
                    if isinstance(user, discord.Member) and not user.bot
                ]
            )
        self._schedule(await self.service.recount(self._origin(message), tuple(reactions)), force=True)
        await self._reply(ctx, _("Starboard votes recounted."))

    async def _named(self, ctx: Context[BotT], name: str) -> StarboardConfig | None:
        assert ctx.guild is not None
        config = await self.service.get(ctx.guild.id, name)
        if config is None:
            await self._reply(ctx, _("No starboard with that name exists."))
        return config

    async def _reply(self, ctx: Context[BotT], message: str, **params: object) -> None:
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(view=text_layout(t(locale, message, **params)), ephemeral=True, allowed_mentions=no_mentions())

    @staticmethod
    def _parse_setting(setting: str, value: str) -> object:
        booleans = {
            "enabled",
            "self_vote",
            "allow_bots",
            "require_image",
            "autoreact_upvote",
            "autoreact_downvote",
            "remove_invalid_reactions",
            "link_edits",
            "link_deletes",
            "jump_to_message",
            "attachments_list",
            "replied_to",
            "ping_author",
        }
        if setting in booleans:
            normalized = value.lower()
            if normalized not in {"true", "false", "on", "off", "yes", "no"}:
                msg = "Boolean settings accept true or false."
                raise ValueError(msg)
            return normalized in {"true", "on", "yes"}
        if setting in {"required", "required_remove"}:
            return float(value)
        if setting in {"min_age_seconds", "max_age_seconds", "colour", "channel_id"}:
            return int(value, 0)
        if setting in {"name", "display_emoji"}:
            return value
        msg = f"Unknown starboard setting: {setting}"
        raise ValueError(msg)


async def setup(bot: "squid.bot.app.RedstoneSquid") -> None:
    await bot.add_cog(StarboardCog(bot))
