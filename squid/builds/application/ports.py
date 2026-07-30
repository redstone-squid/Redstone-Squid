"""Build application ports."""

from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol

from whenever import Instant

from squid.builds.domain import Build


class BuildRepository(Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def save(self, build: Build) -> None: ...

    async def confirm(self, build: Build) -> None: ...

    async def deny(self, build: Build) -> None: ...


class BuildLockManager(Protocol):
    """Coordinate exclusive, task-reentrant access to persisted builds."""

    async def acquire(self, build_id: int, *, blocking: bool, timeout: float) -> bool: ...

    async def release(self, build_id: int) -> None: ...

    def locked(self, build_id: int, *, timeout: float = 30) -> AbstractAsyncContextManager[None]: ...

    async def clean_stale(self, *, older_than: Instant) -> None: ...


class BuildEmbeddingCoordinator(Protocol):
    """Prepare and index build embeddings around relational persistence."""

    async def prepare(self, build: Build) -> None: ...

    async def index(self, build: Build) -> None: ...


class DefaultVersionResolver(Protocol):
    """Resolve the default version used when a build omits compatibility."""

    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str: ...
