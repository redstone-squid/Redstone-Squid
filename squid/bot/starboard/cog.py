"""Discord listeners and configuration commands for starboards."""

import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, guild_only, hybrid_group
from whenever import Instant

from squid.bot.i18n import resolve_locale, t
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.starboard.debounce import EntryDebouncer, EntryKey
from squid.bot.ui import reply_presentation, text_layout
from squid.bot.utils.autocomplete import autocompletes, guild_context, suggests
from squid.bot.utils.permissions import hide_unless, requires
from squid.bot.utils.visibility import personal
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_BOARD_RECOUNT,
    STARBOARD_BOARD_VIEW,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.posts.domain import starboard_entry_key
from squid.reactions.domain import ReactionActor
from squid.starboard.domain import (
    EDITABLE_SETTINGS,
    OriginMessage,
    StarboardConfig,
    StarboardEmoji,
)

if TYPE_CHECKING:
    import squid.bot.app

STARBOARD_CAPABILITIES = (
    STARBOARD_BOARD_VIEW,
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_RECOUNT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
"""Every node this cog gates on.

The group gate is `any` of these rather than the view node alone: each is
separately grantable, so someone handed only `starboard.board.recount` has to be
able to reach the group that contains it.
"""


class StarboardCog[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Mirror messages after their weighted reactions cross configured thresholds."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.service = bot.services.starboards
        self._debouncer = EntryDebouncer(self._refresh_key, bot.background_tasks)
        self.bot.reactions.subscribe(self)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        await self._debouncer.close()

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
        self._schedule(result.keys)

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
        self._schedule(await self.service.refresh(payload.message_id, force=True), force=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # A deleted *post* is tombstoned by the shared message listener and repaired by
        # the reconciler, so only the origin's disappearance is starboard business.
        self._schedule(await self.service.mark_origin_deleted(payload.message_id), force=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.service.disable_channel(channel.id)

    def _schedule(self, keys: Iterable[EntryKey], *, force: bool = False) -> None:
        for key in keys:
            self._debouncer.schedule(key, force=force)

    @staticmethod
    def _origin(message: discord.Message) -> OriginMessage:
        """Read the source-message facts starboard policy is decided from."""
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

    async def _refresh_key(self, key: EntryKey, force: bool) -> None:
        """Nudge the reconciler for one entry.

        The debouncer exists for latency, not correctness: the score write already
        enqueued durable work, so a dropped nudge costs a few seconds rather than a
        missing post. Coalescing reaction storms is what it is actually for.
        """
        del force
        starboard_id, origin_message_id = key
        await self.bot.refresh_posts("starboard_entry", starboard_entry_key(starboard_id, origin_message_id))

    @hybrid_group(name="starboard")
    @guild_only()
    @requires(*STARBOARD_CAPABILITIES, mode="any")
    @hide_unless(manage_guild=True)
    async def starboard_group(self, ctx: Context[BotT]) -> None:
        """Configure weighted message starboards."""
        await ctx.send_help("starboard")

    @starboard_group.command(name="create")
    @requires(STARBOARD_BOARD_CREATE)
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

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @starboard_group.command(name="delete")
    @requires(STARBOARD_BOARD_DELETE)
    async def delete_starboard(self, ctx: Context[BotT], name: str) -> None:
        assert ctx.guild is not None
        deleted = await self.service.delete_starboard(ctx.guild.id, name)
        await self._reply(ctx, _("Starboard deleted.") if deleted else _("No starboard with that name exists."))

    @starboard_group.command(name="list")
    @requires(STARBOARD_BOARD_VIEW)
    async def list_starboards(self, ctx: Context[BotT]) -> None:
        assert ctx.guild is not None
        configs = await self.service.list_for_guild(ctx.guild.id)
        lines = [f"**{item.name}** · <#{item.channel_id}> · {item.required:g}" for item in configs]
        await self._reply(ctx, "\n".join(lines) or _("No starboards are configured."))

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @starboard_group.command(name="show")
    @requires(STARBOARD_BOARD_VIEW)
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

    @autocompletes(
        name=suggests("starboard_names", context=guild_context),
        setting="starboard_settings",
    )
    @starboard_group.command(name="edit")
    @requires(STARBOARD_BOARD_EDIT)
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
    @requires(STARBOARD_BOARD_VIEW, STARBOARD_EMOJI_EDIT, mode="any")
    async def emoji_group(self, ctx: Context[BotT]) -> None:
        """Configure starboard reaction aliases."""
        await ctx.send_help("starboard emoji")

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @emoji_group.command(name="add")
    @requires(STARBOARD_EMOJI_EDIT)
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

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @emoji_group.command(name="remove")
    @requires(STARBOARD_EMOJI_EDIT)
    async def emoji_remove(self, ctx: Context[BotT], name: str, emoji: str) -> None:
        config = await self._named(ctx, name)
        if config is None:
            return
        await self.service.set_emojis(config, tuple(item for item in config.emojis if item.emoji != emoji))
        await self._reply(ctx, _("Starboard emoji removed."))

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @emoji_group.command(name="list")
    @requires(STARBOARD_BOARD_VIEW)
    async def emoji_list(self, ctx: Context[BotT], name: str) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self._reply(
                ctx, "\n".join(f"{item.emoji}: {item.direction} {item.multiplier:g}x" for item in config.emojis)
            )

    @starboard_group.group(name="weight")
    @requires(STARBOARD_BOARD_VIEW, STARBOARD_WEIGHT_EDIT, mode="any")
    async def weight_group(self, ctx: Context[BotT]) -> None:
        """Configure role multipliers."""
        await ctx.send_help("starboard weight")

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @weight_group.command(name="set")
    @requires(STARBOARD_WEIGHT_EDIT)
    async def weight_set(self, ctx: Context[BotT], name: str, role: discord.Role, multiplier: float) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self.service.set_role_multiplier(config, role.id, multiplier)
            await self._reply(ctx, _("Starboard role weight updated."))

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @weight_group.command(name="remove")
    @requires(STARBOARD_WEIGHT_EDIT)
    async def weight_remove(self, ctx: Context[BotT], name: str, role: discord.Role) -> None:
        config = await self._named(ctx, name)
        if config is not None:
            await self.service.set_role_multiplier(config, role.id, None)
            await self._reply(ctx, _("Starboard role weight removed."))

    @autocompletes(name=suggests("starboard_names", context=guild_context))
    @weight_group.command(name="list")
    @requires(STARBOARD_BOARD_VIEW)
    async def weight_list(self, ctx: Context[BotT], name: str) -> None:
        config = await self._named(ctx, name)
        if config is None:
            return
        values = await self.service.get_role_multipliers(config)
        await self._reply(
            ctx, "\n".join(f"<@&{role_id}>: {value:g}x" for role_id, value in values.items()) or _("None")
        )

    @starboard_group.command(name="recount")
    @requires(STARBOARD_BOARD_RECOUNT)
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
        await reply_presentation(
            ctx,
            text_layout(t(locale, message, **params)),
            visibility="personal" if personal(ctx) else "public",
        )

    @staticmethod
    def _parse_setting(setting: str, value: str) -> object:
        match EDITABLE_SETTINGS.get(setting):
            case "boolean":
                normalized = value.lower()
                if normalized not in {"true", "false", "on", "off", "yes", "no"}:
                    msg = "Boolean settings accept true or false."
                    raise ValueError(msg)
                return normalized in {"true", "on", "yes"}
            case "threshold":
                return float(value)
            case "integer":
                return int(value, 0)
            case "text":
                return value
            case _:
                msg = f"Unknown starboard setting: {setting}"
                raise ValueError(msg)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(StarboardCog(bot))
