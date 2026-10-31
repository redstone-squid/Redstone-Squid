"""Bot-owned Discord post application services."""

from collections.abc import Sequence

from squid.posts.application.ports import PostRepository
from squid.posts.domain import DiscordPost, PostReference, ResourceKind, Surface


class PostService:
    """Track which Discord messages the bot owns and how current each one is."""

    def __init__(self, repository: PostRepository):
        self._repository = repository

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
        """Claim a freshly sent Discord message as this resource's post in this channel.

        The unique index makes a second post in the same channel a conflict rather than
        a duplicate card, so a retry that already sent cannot double up.
        """
        await self._repository.record(
            message_id=message_id,
            channel_id=channel_id,
            resource_kind=resource_kind,
            resource_key=resource_key,
            surface=surface,
            applied_revision=applied_revision,
        )

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        """Return every post rendering one resource, suppressed ones included."""
        return await self._repository.list_for_resource(resource_kind, resource_key)

    async def resolve(self, message_id: int) -> PostReference | None:
        """Return what a Discord message renders, for routing reactions back to it."""
        return await self._repository.resolve(message_id)

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Record that one post now shows the given generation."""
        await self._repository.mark_rendered(message_id, applied_revision)

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        """Record that every post for a resource now shows the given generation."""
        await self._repository.mark_applied(resource_kind, resource_key, generation)

    async def suppress(self, message_id: int) -> bool:
        """Note that a post was deleted outside the bot.

        Returns whether a post matched. Suppressing rather than deleting is what lets a
        renderer decide between "a moderator removed this, leave it gone" and "put it
        back", which the two surfaces genuinely disagree about.
        """
        return await self._repository.suppress(message_id)

    async def forget(self, message_id: int) -> None:
        """Drop a post record whose Discord message the bot itself deleted."""
        await self._repository.forget(message_id)

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """Return the generation a resource is waiting on, or None when it is current."""
        return await self._repository.pending_generation(resource_kind, resource_key)
