"""Backend-neutral replicated engine and staging contracts.

`ReplicationEngine` is the SPI a CRDT backend implements once per document; `ReplicaBranch` is
the isolated workspace one Squid transaction edits before the engine commits it. Everything
typed `object` here is backend-defined, so the docstrings are the whole contract.
"""

from typing import Any, Protocol

from squid_reactivity import ConflictDetail


class ReplicaBranch[SnapshotT, OperationT, PreparedT](Protocol):
    """A fork of the engine's canonical state that one transaction stages operations on.

    `ReplicationEngine.branch()` creates one per transaction. Nothing applied here reaches
    canonical state until the engine applies what `prepare()` returns; an aborted transaction
    just drops the branch.
    """

    def apply(self, operation: OperationT) -> None:
        """Stage one `ReplicationEngine.make_operation` result on this branch.

        Applies it to the fork so `snapshot()` reflects it and records it in `operations`.
        Raises `ValueError` when the backend rejects the operation (an unknown kind, an item
        or node that is not present, a text or movable index past the end); Loro's `list`
        container raises `IndexError` for an index past the end instead.
        """
        ...

    def snapshot(self) -> SnapshotT:
        """The canonical snapshot plus every staged operation; the same immutable type as the engine's."""
        ...

    def prepare(self, base: object) -> PreparedT:
        """Seal the staged operations into the value `ReplicationEngine.apply` commits verbatim.

        `base` is this branch's own `base`, handed back by the participant after it has
        confirmed the engine still reports it. Raises `RuntimeError` if the engine has moved
        on anyway, and `ValueError` when the backend's size limits are exceeded (Loro: a 1 MiB
        update, 10 000 operations, 256 root containers, 100 000 items in one container or an
        8 MiB snapshot). Loro also raises `ReplicationBackendIntegrityError` when its own
        fork fails to commit or export.
        """
        ...

    def stage_inverse(self, inverse: object) -> None:
        """Stage an inverse produced by `ReplicationEngine.plan_inverse`, appending to `operations`.

        Raises `TypeError` when the inverse comes from a different backend; Loro also raises
        `ReplicationBackendIntegrityError` when it rejects the planned diff.
        """
        ...

    @property
    def base(self) -> object:
        """The engine `version()` this branch forked from; equal to it until someone else commits."""
        ...

    @property
    def operations(self) -> tuple[OperationT, ...]:
        """Every operation staged so far, in order, for `ReplicationEngine.visible_tags` to count as applied."""
        ...


class ReplicationEngine[SnapshotT, OperationT, PreparedT, ChangeT](Protocol):
    """One document's CRDT state behind immutable snapshots and opaque tokens.

    `ReplicatedDocument` owns exactly one engine and is its only caller. Every `object`-typed
    value is backend-defined and round-trips only through the same backend.
    """

    @property
    def backend_id(self) -> str:
        """A stable string stamped into every update envelope and durable token; mismatches are refused on import."""
        ...

    @property
    def replica_id(self) -> str:
        """The incarnation name `ReplicationBackend.open_engine` received; recorded as each outbound update's source."""
        ...

    @property
    def container_kinds(self) -> frozenset[str]:
        """The subset of `counter`, `set`, `text`, `list`, `movable`, `map`, `tree` this backend implements.

        `ReplicatedDocument` raises `UnsupportedReplicationContainerError` for any other kind
        before an operation is built.
        """
        ...

    def snapshot(self) -> SnapshotT:
        """The canonical committed state, deeply immutable: it is cached in a cell and handed to subscribers."""
        ...

    def version(self) -> object:
        """An opaque, hashable, equality-comparable marker of the canonical state.

        Equal exactly when no commit has happened in between; it is compared with
        `ReplicaBranch.base` before every prepare. Peers receive it from
        `ReplicatedDocument.version()` and hand it back to `export_since`.
        """
        ...

    def branch(self) -> ReplicaBranch[SnapshotT, OperationT, PreparedT]:
        """A new isolated branch whose `base` is the current `version()`."""
        ...

    def apply(self, prepared: PreparedT) -> ChangeT:
        """Commit a prepared value into canonical state, all or nothing.

        Called once per transaction at the commit point, after `change_token` answered
        non-`None`. Raises `ReplicationBackendIntegrityError` when the backend rejects what
        its own branch prepared, and `ValueError` when an operation identity is reused with
        different content; either way canonical state is untouched.
        """
        ...

    def prepare_remote(self, update: bytes) -> PreparedT:
        """Validate a peer's `encode_prepared` or `export_since` bytes without touching canonical state.

        The result goes through `change_token` (which answers `None` when the update adds
        nothing new, so duplicates are skipped) and then `apply`. Raises
        `ReplicationCorruptUpdateError` for bytes the backend cannot decode or that decode to
        invalid replicated state, and `ValueError` past the backend's size limit.
        """
        ...

    def export_since(self, version: object | None = None) -> bytes:
        """Everything a peer at `version` lacks, as bytes its `prepare_remote` accepts.

        `None` means the peer has nothing: a self-contained snapshot. Raises `TypeError` when
        `version` is not this backend's type, `ValueError` when it names history this engine
        has never seen, `ReplicationCorruptUpdateError` when it does not decode, and
        `ReplicationBackendIntegrityError` when the export itself fails.
        """
        ...

    def change_token(self, prepared: PreparedT) -> object | None:
        """The backend token history keeps to reverse `prepared`, or `None` when it changes nothing.

        `None` makes the participant skip apply, notification and outbound publishing. The
        token is wrapped in `ReplicationChangeToken` and can outlive the transaction, so it
        must not reference branch state.
        """
        ...

    def encode_token(self, token: object) -> bytes:
        """Serialise a `change_token` result for durable history; `decode_token` reverses it on any replica.

        Raises `TypeError` for a token from another backend and `ValueError` past the size limit.
        """
        ...

    def decode_token(self, token: bytes) -> object:
        """Reload `encode_token` bytes, treating them as untrusted.

        Raises `ReplicationCorruptUpdateError`, `ValueError` or `TypeError` for anything that
        is not a well-formed token of this backend; `ReplicationChangeToken.decode` folds all
        three into `ValueError`.
        """
        ...

    def plan_inverse(self, token: object) -> object | ConflictDetail:
        """Build the opaque inverse `ReplicaBranch.stage_inverse` accepts, without mutating state.

        Returns a `ConflictDetail` instead when the token cannot be reversed here: it belongs to
        another backend or backend version, `compact()` has discarded the history it needs
        (`replicated:loro:expired`), or a later operation has taken authority over an item,
        key or node the token touched. A conflict never raises.
        """
        ...

    def encode_prepared(self, prepared: PreparedT) -> bytes:
        """The payload of the outbound `ReplicationUpdate`; a peer's `prepare_remote` must accept it."""
        ...

    def make_operation(self, kind: str, path: str, **data: Any) -> OperationT:
        """Build the operation `ReplicaBranch.apply` stages for one container mutation.

        The containers in `document` fix the kinds and their `data` keys: `increment(value)`,
        `add(value)`, `remove(value, tags)`, `text_insert(index, value)`,
        `text_delete(index, count)`, `list_insert/list_replace(index, value)`,
        `list_delete(index, count)`, `movable_insert(index, item_id, value)`,
        `movable_delete(item_id)`, `movable_move(item_id, index)`, `movable_replace(item_id,
        value)`, `map_set(key, value)`, `map_delete(key)`, `tree_create(node_id, parent_id,
        index, metadata)`, `tree_move(node_id, parent_id, index)`, `tree_metadata(node_id, key,
        value)` and `tree_delete(node_id)`. Values arrive already frozen by `freeze_value`;
        identities arrive as strings. Raises `ValueError` for a path past the backend's limit.
        """
        ...

    def visible_tags(
        self,
        path: str,
        value: str,
        additions: tuple[OperationT, ...] = (),
    ) -> tuple[object, ...]:
        """The add-tags that currently make `value` a member of the set at `path`.

        `additions` are the current branch's staged operations, counted as applied so a removal
        sees an add from the same transaction. `ReplicatedSet.discard` records these tags on
        the `remove` so it cancels exactly the adds it observed and a concurrent add survives.
        Empty when `value` is not a member.
        """
        ...

    def retain_token(self, token: object) -> object:
        """Pin the history `plan_inverse` needs for `token` past `compact()`; returns a handle for `release_token`.

        `ReplicationHistoryLease` holds the handle and releases it explicitly or on collection;
        an unreleased handle keeps `compact()` from discarding anything at or after the token
        forever. Raises `ValueError` when the token is foreign or its history is already gone.
        """
        ...

    def release_token(self, retention: object) -> None:
        """Drop a `retain_token` handle; unknown or already-released handles are ignored."""
        ...

    def compact(self) -> None:
        """Discard history older than every retained token's boundary.

        Afterwards `plan_inverse` answers an expired `ConflictDetail` for any unretained token
        older than that boundary. `ReplicatedDocument.compact_history` only calls this outside
        a transaction. Raises `ReplicationBackendIntegrityError` when a retained boundary is no
        longer reachable.
        """
        ...


class ReplicationBackend(Protocol):
    """A configured engine factory reusable for every document in one replica."""

    @property
    def backend_id(self) -> str:
        """The `backend_id` every engine this backend opens reports."""
        ...

    def open_engine(
        self,
        replica_id: str,
        document_id: str,
    ) -> Any:
        """A fresh `ReplicationEngine` for one document; `Replica.open` calls it once per `document_id`."""
        ...
