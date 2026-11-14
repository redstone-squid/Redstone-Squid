"""Identify the intent and causal context of reactive work.

TransactionContext gives a transaction an identity and links it to the event that
caused it. Operations and resource loads carry causal references so their later
results can still be traced to the initiating transaction. Transaction outcomes and
reversible changes are defined in transactions.results.
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Literal

type TransactionId = uuid.UUID
_EMPTY_METADATA: Mapping[str, str] = MappingProxyType({})


class TransactionPurpose(Enum):
    """Why a transaction was started."""

    NORMAL = "normal"
    UNDO = "undo"
    REDO = "redo"
    COMPENSATION = "compensation"
    REMOTE = "remote"
    RECOVERY = "recovery"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class CausalRef:
    """A stable reference to a transaction, operation, resource generation, or system event."""

    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class ActorRef:
    """An application-defined actor identifier included in diagnostics."""

    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class TransactionRelation:
    """Identify an earlier transaction being reversed, reapplied, or compensated."""

    kind: Literal["reverses", "reapplies", "compensates"]
    transaction_id: TransactionId


@dataclass(frozen=True, slots=True)
class TransactionContext:
    """Immutable identity and causality assigned before a transaction begins."""

    transaction_id: TransactionId
    cause: CausalRef | None
    root_transaction_id: TransactionId
    kind: TransactionPurpose
    name: str
    actor: ActorRef | None
    started_at: datetime
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)
    relation: TransactionRelation | None = None

    @classmethod
    def create(
        cls,
        name: str = "transaction",
        *,
        kind: TransactionPurpose = TransactionPurpose.NORMAL,
        cause: CausalRef | None = None,
        root_transaction_id: TransactionId | None = None,
        actor: ActorRef | None = None,
        metadata: Mapping[str, str] | None = None,
        relation: TransactionRelation | None = None,
    ) -> TransactionContext:
        transaction_id = uuid.uuid7()
        return cls(
            transaction_id,
            cause,
            root_transaction_id or transaction_id,
            kind,
            name,
            actor,
            datetime.now(UTC),
            _EMPTY_METADATA if not metadata else MappingProxyType(dict(metadata)),
            relation,
        )

    def causal_ref(self) -> CausalRef:
        """Return the immutable token detached work may retain."""
        return CausalRef("transaction", str(self.transaction_id))


@dataclass(frozen=True, slots=True)
class ExceptionReport:
    """An exception type and message without references to its traceback.

    The message is captured unchanged and may contain sensitive data. Apply a
    RedactionPolicy before storing or sharing the report.
    """

    type_name: str
    message: str

    @classmethod
    def capture(cls, error: BaseException) -> ExceptionReport:
        return cls(type(error).__name__, str(error))


@dataclass(frozen=True, slots=True)
class Causality:
    """Origin captured by detached work, independent of any writable transaction."""

    cause: CausalRef
    root_transaction_id: TransactionId | None


_CURRENT_CAUSALITY: ContextVar[Causality | None] = ContextVar("squid_reactivity_causality", default=None)
"""Origin for implicitly created resource generations and transactions.

Transactions install their identity through publication and outcome delivery. Operations
install their captured execution identity while their loader runs. Child tasks inherit this
immutable origin; it grants no permission to write through the originating transaction.
"""

_CONTINUATION: ContextVar[bool] = ContextVar("squid_reactivity_continuation", default=False)
"""Result callbacks must open a new transaction before writing reactive state.

Nested callback scopes restore the outer guard on exit. The guard is inherited by child
tasks, so scheduling work does not accidentally permit direct writes from a callback.
"""


def current_causality() -> Causality | None:
    """Return the origin to capture when starting implicit reactive work."""
    return _CURRENT_CAUSALITY.get()


@contextmanager
def causal_scope(cause: CausalRef, root_transaction_id: TransactionId | None) -> Iterator[None]:
    """Set cause and root identifiers without opening a transaction."""
    token = _CURRENT_CAUSALITY.set(Causality(cause, root_transaction_id))
    try:
        yield
    finally:
        _CURRENT_CAUSALITY.reset(token)


@contextmanager
def continuation_callback() -> Iterator[None]:
    """Mark this with block as continuation work after a transaction result."""
    token = _CONTINUATION.set(True)
    try:
        yield
    finally:
        _CONTINUATION.reset(token)


def in_continuation() -> bool:
    return _CONTINUATION.get()
