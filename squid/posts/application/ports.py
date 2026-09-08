"""Bot-owned Discord post application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.posts.domain import DiscordPost, PostReference, ResourceKind, Surface


class PostRepository(Protocol):
    """Persistence required by `PostService`."""

    async def record(
        self,
        *,
        message_id: int,
        channel_id: int,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
        applied_revision: int,
    ) -> None:
        """Claim a sent message as this resource's post in this channel.

        Recording the same message twice is a no-op. A second live message for the same resource and channel
        violates a unique index, which is what stops a retry posting a duplicate card.
        """
        ...

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        """Every post rendering the resource, suppressed ones included, by channel id."""
        ...

    async def resolve(self, message_id: int) -> PostReference | None:
        """What the message renders, or `None` if the bot does not own it."""
        ...

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Advance one post's applied revision. Never moves it backwards, so an overlapping slower pass is ignored."""
        ...

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        """Advance every post for the resource to `generation`, leaving any already ahead of it alone."""
        ...

    async def suppress(self, message_id: int) -> bool:
        """Tombstone a post deleted outside the bot, freeing its slot in the unique index.

        Returns whether a live post matched.
        """
        ...

    async def forget(self, message_id: int) -> None:
        """Delete the post record outright, for a message the bot itself deleted."""
        ...

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """The queued generation the resource's live posts have not all reached, or `None` when they are current."""
        ...
