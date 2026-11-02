"""Bot-owned Discord post application services."""

from collections.abc import Sequence

from squid.posts.application.ports import PostUnitOfWork
from squid.posts.domain import PostReference, ResourceKind, Surface
from squid.posts.models import DiscordPost
from squid.unit_of_work import UnitOfWorkFactory


class PostService:
    """Track published posts and the revisions they display."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory[PostUnitOfWork]) -> None:
        self._uow_factory = uow_factory

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
        async with self._uow_factory() as uow:
            await uow.posts.record(
                message_id=message_id,
                channel_id=channel_id,
                resource_kind=resource_kind,
                resource_key=resource_key,
                surface=surface,
                applied_revision=applied_revision,
            )
            await uow.commit()

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        """Return every post rendering one resource, suppressed ones included."""
        async with self._uow_factory() as uow:
            return await uow.posts.list_for_resource(resource_kind, resource_key)

    async def resolve(self, message_id: int) -> PostReference | None:
        """Return the resource displayed by a message, or None if the post is unknown."""
        async with self._uow_factory() as uow:
            return await uow.posts.resolve(message_id)

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        """Advance a post's rendered generation if the supplied value is newer."""
        async with self._uow_factory() as uow:
            await uow.posts.mark_rendered(message_id, applied_revision)
            await uow.commit()

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None:
        """Advance all posts for a resource to at least the supplied generation."""
        async with self._uow_factory() as uow:
            await uow.posts.mark_applied(resource_kind, resource_key, generation)
            await uow.commit()

    async def suppress(self, message_id: int) -> bool:
        """Record an external deletion, returning True if a live post was suppressed."""
        async with self._uow_factory() as uow:
            changed = await uow.posts.suppress(message_id)
            await uow.commit()
            return changed

    async def forget(self, message_id: int) -> None:
        """Remove the tracking record for a post."""
        async with self._uow_factory() as uow:
            await uow.posts.forget(message_id)
            await uow.commit()

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None:
        """Return the queued generation if a live post is behind, otherwise None."""
        async with self._uow_factory() as uow:
            return await uow.posts.pending_generation(resource_kind, resource_key)
