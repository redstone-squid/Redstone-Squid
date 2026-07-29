"""Build application ports."""

from datetime import datetime
from typing import Literal, Protocol

from squid.builds.domain import Build


class BuildRepository(Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def save(self, build: Build) -> None: ...

    async def confirm(self, build: Build) -> None: ...

    async def deny(self, build: Build) -> None: ...

    async def acquire_lock(self, build_id: int, *, blocking: bool, timeout: float) -> bool: ...

    async def release_lock(self, build_id: int) -> None: ...

    async def update_smallest_door_records_without_title(self) -> None: ...

    async def clean_stale_locks(self, *, older_than: datetime) -> None: ...


class BuildEmbeddingCoordinator(Protocol):
    """Prepare and index build embeddings around relational persistence."""

    async def prepare(self, build: Build) -> None: ...

    async def index(self, build: Build) -> None: ...


class DefaultVersionResolver(Protocol):
    """Resolve the default version used when a build omits compatibility."""

    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str: ...
