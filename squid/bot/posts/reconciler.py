"""Reconcile recorded Discord posts with the content and destinations a renderer requests.

A resource is the application entity being shown, such as a build, voting session, or starboard
entry. Its renderer reads the current state and describes the posts it should have in each channel.
PostReconciler compares that answer with the post registry, then sends, edits, or removes messages.
Commands can request an immediate refresh; ReconciliationCog runs the same workflow from durable
queued work so failed delivery can be retried.

Each refresh has a queue generation. A post's applied_revision records the generation it last
rendered, allowing an already-current post to skip an edit. This is separate from a build's edit
revision. Discord writes and registry writes are separate operations, so a failure after sending
can leave a message that has not yet been recorded.
"""

import contextlib
import logging
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING

import discord

import squid_ui_discord as sd
from squid.bot.message_adapter import to_message_fact
from squid.bot.posts.renderer import DesiredPost, PostRenderer
from squid.core.concurrency import DISCORD_FANOUT_LIMIT, run_all
from squid.posts.domain import ResourceKind, Surface
from squid.posts.models import DiscordPost
from squid_ui_discord import send_to

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
        """Register an already-sent message so future reconciliation can update it.

        Callers use this for cards whose destination a person chose, such as a
        delete-log vote. The initial applied revision is zero.
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
        """Apply one renderer snapshot using `generation` to skip already-current edits.

        Missing renderers and unreachable destinations are skipped. Deletion also
        tolerates missing messages and denied access. Other transport or persistence
        errors propagate; the durable caller can retry the resource.
        """
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
            # No live post was recorded, or editing could not fetch its message.
            # The renderer decides whether a previously recorded post may be replaced.
            was_removed = post is not None or want.channel_id in suppressed
            if was_removed and not renderer.repost_if_deleted:
                # Preserve suppression for surfaces where deletion ends publication.
                continue
            await self._send(renderer, resource_kind, resource_key, want, generation)

        # Remove live posts from destinations the renderer no longer requests.
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
        result = await send_to(channel, allowed_mentions=want.allowed_mentions)(want.payload)
        message = result.message
        if message is None:
            detail = "channel post delivery returned no message"
            raise RuntimeError(detail)
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
        """Return whether the post needs no replacement after applying this generation.

        An already-current revision returns `True` without checking Discord.
        Otherwise an unavailable message returns `False`; this includes missing
        channels and denied access, not only deleted messages.
        """
        if post.applied_revision >= generation:
            return True
        message = await self._fetch(post)
        if message is None:
            return False
        await sd.edit_to(message)(want.payload)
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
        """Fetch a recorded message, returning `None` when unavailable.

        Discord `NotFound` suppresses the post and marks the message deleted.
        An unavailable channel or `Forbidden` returns without changing those rows.
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
