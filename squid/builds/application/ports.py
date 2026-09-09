"""Build application ports."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from squid.builds.domain import Build


@dataclass(frozen=True, slots=True)
class BuildSchematicSummary:
    """The handful of schematic facts a build card needs, in plain scalars.

    This is the whole of what `builds` may know about schematics; the analysis read model stays
    in the schematic context.
    """

    width: int
    height: int
    length: int
    block_count: int
    palette_size: int
    source_data_version: int | None = None
    lattice_label: str | None = None
    sign_texts: tuple[str, ...] = ()


class BuildRepository(Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None:
        """The build with this ID, or None when no row has it."""
        ...

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        """The build already created from a synchronized submission draft, or None."""
        ...

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        """Every build attributed to one Discord message, in ascending build-ID order.

        Plural because one build-log message routinely yields a bundle.
        """
        ...

    async def save(self, build: Build) -> None:
        """Insert or update the build, stamping `edited_time` and refreshing `id` and `revision`.

        Raises:
            BuildRevisionMismatchError: If the stored revision has moved past `build.revision`.
        """
        ...

    async def confirm(self, build: Build) -> None:
        """Move the build to `Status.CONFIRMED` and bump its revision in place.

        Raises:
            BuildRevisionMismatchError: If the stored revision has moved past `build.revision`.
        """
        ...

    async def deny(self, build: Build) -> None:
        """Move the build to `Status.DENIED` and bump its revision in place.

        Raises:
            BuildRevisionMismatchError: If the stored revision has moved past `build.revision`.
        """
        ...


class BuildLockManager(Protocol):
    """Coordinate exclusive, context-reentrant access to persisted builds."""

    async def acquire(self, build_id: int, *, blocking: bool, timeout: float) -> bool:
        """Take the build's lease, or re-enter it when the calling context already holds it.

        Returns False rather than raising when the lease is held elsewhere and `blocking` is
        False or `timeout` elapses. A negative `timeout` waits forever.
        """
        ...

    async def release(self, build_id: int) -> None:
        """Drop one level of the calling context's lease, clearing the persisted lock at depth zero.

        Raises:
            InvalidStateError: If a context that does not hold the lease releases it.
        """
        ...

    def locked(self, build_id: int, *, timeout: float = 30) -> AbstractAsyncContextManager[None]:
        """Hold the build's lease for the duration of the block.

        Raises:
            BuildBusyError: If the lease cannot be taken within `timeout`.
        """
        ...

    async def clean_stale(self) -> None:
        """Reclaim persisted locks whose stored expiry has passed and forget the leases naming them.

        The expiry column is the only cutoff; callers do not choose one.
        """
        ...


class BuildEmbeddingCoordinator(Protocol):
    """Prepare and index build embeddings around relational persistence."""

    async def prepare(self, build: Build) -> None:
        """Attach an embedding to the build before it is written; a no-op when none can be generated."""
        ...

    async def index(self, build: Build) -> None:
        """Index an already-persisted build, skipping one with no ID or no embedding."""
        ...


class DefaultVersionResolver(Protocol):
    """Resolve the default version used when a build omits compatibility."""

    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str:
        """The newest known version string for an edition."""
        ...


class BuildSchematicSummaryProvider(Protocol):
    """Supply machine-read schematic facts for a build, if it has any.

    The single seam between `builds` and the schematic context.
    """

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None:
        """The primary schematic's facts, or None when the build has none or the engine is absent."""
        ...
