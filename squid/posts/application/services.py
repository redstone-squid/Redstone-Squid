"""Bot-owned Discord post application services."""

from collections.abc import Sequence

from squid.posts.application.ports import PostRepository
from squid.posts.domain import PostReference, ResourceKind, Surface
from squid.posts.models import DiscordPost


class PostService:
    """Track published posts and the revisions they display."""

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
        """Record a sent post. An existing message ID is left unchanged.

        Each resource can have one live post per channel.
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
        """Return the resource displayed by a message, or None if the post is unknown."""
        return await self._repository.resolve(message_id)

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Advance a post's rendered generation if the supplied value is newer."""
        await self._repository.mark_rendered(message_id, applied_revision)

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        """Advance all posts for a resource to at least the supplied generation."""
        await self._repository.mark_applied(resource_kind, resource_key, generation)

    async def suppress(self, message_id: int) -> bool:
        """Record an external deletion, returning True if a live post was suppressed."""
        return await self._repository.suppress(message_id)

    async def forget(self, message_id: int) -> None:
        """Remove the tracking record for a post."""
        await self._repository.forget(message_id)

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """Return the queued generation if a live post is behind, otherwise None."""
        return await self._repository.pending_generation(resource_kind, resource_key)
