"""Bot-owned Discord post application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.posts.domain import DiscordPost, PostReference, ResourceKind, Surface


class PostRepository(Protocol):
    """Persistence required by :class:`PostService`."""

    async def record(
        self,
        *,
        message_id: int,
        channel_id: int,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
        applied_revision: int,
    ) -> None: ...

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]: ...

    async def resolve(self, message_id: int) -> PostReference | None: ...

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None: ...

    async def mark_applied(self, resource_kind: ResourceKind, resource_key: str, generation: int) -> None: ...

    async def suppress(self, message_id: int) -> bool: ...

    async def forget(self, message_id: int) -> None: ...

    async def pending_generation(self, resource_kind: ResourceKind, resource_key: str) -> int | None: ...
