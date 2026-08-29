"""Bring the bot's own Discord posts back in line with what a resource wants.

One diff loop serves every surface. It replaces the per-surface idempotency schemes
that grew up separately: an "already posted?" set in the confirmed-build handler, a
stable-nonce send in the review session, and a posted-message-id column in the
starboard entry table. All three were answering "does a post already exist here?",
which is now a unique index.
"""

import contextlib
import logging
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING

import discord

from squid.bot.message_adapter import to_message_fact
from squid.bot.posts.renderer import DesiredPost, PostRenderer
from squid.bot.utils.components import edit_layout
from squid.core.concurrency import DISCORD_FANOUT_LIMIT, run_all
from squid.posts.domain import DiscordPost, ResourceKind, Surface

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class PostReconciler[BotT: "squid.bot.app.RedstoneSquid"]:
    """Diff the posts a resource wants against the posts it has."""

    def __init__(self, bot: BotT, renderers: Sequence[PostRenderer]) -> None:
        self.bot = bot
        self._renderers = {renderer.resource_kind: renderer for renderer in renderers}

    def handles(self, resource_kind: ResourceKind) -> bool:
        """Whether a renderer is registered for this kind of resource."""
        return resource_kind in self._renderers

    async def adopt(
        self,
        message: discord.Message,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
    ) -> None:
        """Take ownership of a message someone else already sent.

        Some cards go where a person chose rather than where configuration says — a
        delete-log vote, a published poll — so the command sends the message and hands
        it over. From then on it is an ordinary post the diff loop keeps rendered.
        """
        await self.bot.services.messages.observe(to_message_fact(message))
        await self.bot.services.posts.record(
            message_id=message.id,
            channel_id=message.channel.id,
            resource_kind=resource_kind,
            resource_key=resource_key,
            surface=surface,
            applied_revision=0,
        )

    async def reconcile(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        """Send, edit, or delete posts until Discord matches the renderer's answer."""
        renderer = self._renderers.get(resource_kind)
        if renderer is None:
            logger.warning("No renderer is registered for %s posts", resource_kind)
            return
        existing = await self.bot.services.posts.list_for_resource(resource_kind, resource_key)
        desired = await renderer.desired(resource_key)

        if desired is None:
            await self._remove_all(existing)
            return

        live = {post.channel_id: post for post in existing if post.is_live}
        suppressed = {post.channel_id for post in existing if not post.is_live}
        wanted_channels = {post.channel_id for post in desired}

        for want in desired:
            post = live.get(want.channel_id)
            if post is not None and await self._edit(post, want, generation):
                continue
            # Either there was never a post here, or the one there is has vanished and
            # was just tombstoned. Both mean the channel now needs a fresh post.
            was_removed = post is not None or want.channel_id in suppressed
            if was_removed and not renderer.repost_if_deleted:
                # Someone removed this post deliberately; putting it back would be an
                # argument with a moderator, not a repair.
                continue
            await self._send(renderer, resource_kind, resource_key, want, generation)

        # A post whose channel is no longer wanted is stale in the strongest sense: the
        # resource says nothing belongs here any more.
        await self._remove_all([post for channel_id, post in live.items() if channel_id not in wanted_channels])

    async def _send(
        self,
        renderer: PostRenderer,
        resource_kind: ResourceKind,
        resource_key: str,
        want: DesiredPost,
        generation: int,
    ) -> None:
        channel = await self.bot.get_or_fetch_messageable_channel(want.channel_id)
        if channel is None:
            logger.debug("Skipping a post to an unreachable channel %s", want.channel_id)
            return
        message = await channel.send(view=want.layout, allowed_mentions=want.allowed_mentions)
        # The fact has to land before the post: `discord_posts.message_id` is RESTRICT,
        # so a post row cannot reference a message the database has not recorded yet.
        await self.bot.services.messages.observe(to_message_fact(message))
        await self.bot.services.posts.record(
            message_id=message.id,
            channel_id=want.channel_id,
            resource_kind=resource_kind,
            resource_key=resource_key,
            surface=want.surface,
            applied_revision=generation,
        )
        await renderer.after_send(resource_key, message)

    async def _edit(self, post: DiscordPost, want: DesiredPost, generation: int) -> bool:
        """Bring one post up to date, reporting whether it still exists.

        False means Discord no longer has the message, so the caller decides between
        reposting and leaving the channel empty. Repairing it in this pass rather than
        waiting to be enqueued again is what keeps a mirrored surface prompt.
        """
        if post.applied_revision >= generation:
            return True
        message = await self._fetch(post)
        if message is None:
            return False
        await edit_layout(message, want.layout, allowed_mentions=want.allowed_mentions)
        await self.bot.services.posts.mark_rendered(post.message_id, generation)
        return True

    async def _remove_all(self, posts: Sequence[DiscordPost]) -> None:
        await run_all([partial(self._remove, post) for post in posts], limit=DISCORD_FANOUT_LIMIT)

    async def _remove(self, post: DiscordPost) -> None:
        message = await self._fetch(post)
        if message is not None:
            # Already gone is the expected state on a retry.
            with contextlib.suppress(discord.NotFound, discord.Forbidden):
                await message.delete()
        await self.bot.services.posts.forget(post.message_id)

    async def _fetch(self, post: DiscordPost) -> discord.Message | None:
        """Resolve a post's message, tombstoning it if Discord says it is gone.

        This is the one place a read still writes, and it is confined to the reconcile
        loop rather than sitting inside the shared message fetcher where every caller
        paid for it.
        """
        channel = await self.bot.get_or_fetch_messageable_channel(post.channel_id)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(post.message_id)
        except discord.NotFound:
            await self.bot.services.posts.suppress(post.message_id)
            await self.bot.services.messages.mark_deleted(post.message_id)
            return None
        except discord.Forbidden:
            return None
