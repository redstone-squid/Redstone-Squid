"""Build application ports."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from whenever import Instant

from squid.builds.history import BuildEditActor, HistoryOperation
from squid.builds.models import Build
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class BuildSchematicSummary:
    """Schematic measurements and sign text displayed on a build card.

    This small value keeps build rendering independent of the full schematic
    analysis model. SchematicServiceSummaryProvider selects these fields from
    that model when a featured schematic is available.
    """

    width: int
    height: int
    length: int
    block_count: int
    palette_size: int
    source_data_version: int | None = None
    lattice_label: str | None = None
    sign_texts: tuple[str, ...] = ()


class BuildEditing(Protocol):
    """Declare attribution once for all build writes in the current transaction."""

    def editing(
        self,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> AbstractAsyncContextManager[None]:
        """Attribute all build changes in this transaction to an account ID or named system actor.

        Open one scope before mutation. Scope exit flushes changes; the transaction owner
        commits inside or after the scope. Exceptions roll back, and nested scopes fail.
        Meaningful build writes outside the scope are rejected by the database.
        """
        ...


class BuildEditingUnitOfWork(UnitOfWork, Protocol):
    @property
    def builds(self) -> BuildEditing: ...


class BuildRepository(BuildEditing, Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def get_for_update(self, build_id: int) -> Build | None: ...

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None: ...

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]: ...

    async def save(self, build: Build) -> None: ...

    async def confirm(self, build: Build) -> None: ...

    async def deny(self, build: Build) -> None: ...


class BuildLockManager(Protocol):
    """Coordinate build leases that child tasks can inherit and reenter."""

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
    """Read the featured schematic facts needed to display a build."""

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None: ...


class BuildUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def builds(self) -> BuildRepository: ...
