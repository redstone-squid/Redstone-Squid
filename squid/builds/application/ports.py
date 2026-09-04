"""Build application ports."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from whenever import Instant

from squid.builds.domain import Build


@dataclass(frozen=True, slots=True)
class BuildSchematicSummary:
    """The handful of schematic facts a build card needs, in plain scalars.

    Deliberately not the schematic read model: this is the whole of what `builds` is allowed to
    know about schematics, so the two contexts stay decoupled and a change to the analysis
    shape cannot ripple into build rendering.
    """

    width: int
    height: int
    length: int
    block_count: int
    palette_size: int
    source_data_version: int | None = None
    lattice_label: str | None = None
    sign_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceSubmissionBuildWrite:
    """Canonical build selected for a source draft and whether this call created it."""

    build: Build
    created: bool


class BuildRepository(Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None: ...

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]: ...

    async def save(self, build: Build) -> None: ...

    async def save_for_source_submission(self, build: Build) -> SourceSubmissionBuildWrite: ...

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


class BuildSchematicSummaryProvider(Protocol):
    """Supply machine-read schematic facts for a build, if it has any.

    The single seam between `builds` and the schematic context. `BuildService` never learns
    what a schematic is; it asks this and gets scalars or nothing.
    """

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None: ...
