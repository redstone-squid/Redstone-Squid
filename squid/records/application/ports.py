"""Ports used by record application services."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.records.application.models import (
    CategoryIdentity,
    ComputationBatch,
    PublishedRecord,
    RecordGap,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.domain import BuildKind, CategoryText, RecordClass


@dataclass(frozen=True, slots=True)
class RecomputeLease:
    """A set of recompute scopes leased together by one worker.

    The kinds are what gets rebuilt; the claim tokens fence the acknowledgement, distinguishing
    the rows this worker leased from rows enqueued for the same kind while it ran. The lease ends
    at `complete_recompute` or `fail_recompute`, or when the queue's visibility timeout reclaims
    the rows.
    """

    kinds: tuple[BuildKind, ...]
    claim_tokens: tuple[uuid.UUID, ...]

    def __bool__(self) -> bool:
        return bool(self.kinds)


class RecordCandidateRepository(Protocol):
    """Load confirmed builds eligible to become computation candidates."""

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]:
        """Return every confirmed build of `kind`, or nothing for a kind that holds no records."""
        ...


class RecordRunRepository(Protocol):
    """Persist and inspect versioned record computation runs."""

    async def active_ruleset_id(self) -> int:
        """Return the active ruleset id, activating the running calculator and formatter versions."""
        ...

    async def active_current_version_id(self) -> int | None:
        """Return the version pinned by the newest active current-scope run, or None when none is."""
        ...

    async def activate(self, batch: ComputationBatch) -> int:
        """Persist `batch`, make it the sole active run for its kind and scope, and return its id."""
        ...

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """Return the decisive missing facts of unresolved results in the active runs."""
        ...

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        """Return active definitions whose canonical title carries formatter diagnostics."""
        ...

    async def get_published_record(self, result_id: int) -> PublishedRecord | None:
        """Return one result of an active run, or None when no active run publishes that id."""
        ...

    async def list_published_records(
        self,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        descending: bool,
        limit: int,
    ) -> Sequence[PublishedRecord]:
        """Return active-run results in display order, anchored by `after_id`, `before_id` or `offset`.

        A `before_id` page ends up in display order, so its extra overfetched row is at the front.
        """
        ...

    async def count_published_records(self) -> int:
        """Count the results belonging to the currently active runs."""
        ...

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        """Return the exact categories of `kind` materialized through public lookup."""
        ...

    async def get_definition_identity(self, definition_id: int) -> CategoryIdentity | None:
        """Return the category identity of one definition, or None when no definition has that id.

        Raises:
            DataIntegrityError: If the stored category key does not parse.
        """
        ...

    async def save_requested_category(
        self,
        ruleset_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordClass, CategoryText],
    ) -> None:
        """Persist a definition per record class so later rebuilds keep materializing `category`."""
        ...

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None:
        """Request a durable rebuild of `kind`; repeated requests for one scope collapse into one."""
        ...

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease:
        """Lease at most `limit` queued rows, returning their kinds and fencing tokens."""
        ...

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        """Acknowledge the leased rows whose fencing tokens the worker still owns."""
        ...

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        """Release the lease's rows for a later retry, recording `error` against them."""
        ...
