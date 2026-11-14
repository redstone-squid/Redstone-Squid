"""Identify the intent and causal context of reactive work.

TransactionContext gives a transaction an identity and links it to the event that
caused it. Operations and resource loads carry causal references so their later
results can still be traced to the initiating transaction. Transaction outcomes and
reversible changes are defined in transaction_results.
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


_CURRENT_TRANSACTION_CONTEXT: ContextVar[TransactionContext | None] = ContextVar(
    "squid_reactivity_transaction_context", default=None
)
_CURRENT_CAUSALITY: ContextVar[tuple[CausalRef, TransactionId | None] | None] = ContextVar(
    "squid_reactivity_causality", default=None
)
_continuation_depth: ContextVar[int] = ContextVar("squid_reactivity_continuation_depth", default=0)


def current_transaction_context() -> TransactionContext | None:
    """Return the transaction context installed in this context, if any."""
    return _CURRENT_TRANSACTION_CONTEXT.get()


def current_causality() -> tuple[CausalRef, TransactionId | None] | None:
    """Return the immutable cause/root pair installed for transaction, operation, or resource work."""
    return _CURRENT_CAUSALITY.get()


@contextmanager
def transaction_context_scope(context: TransactionContext) -> Iterator[TransactionContext]:
    """Install a transaction context until the with block exits."""
    token = _CURRENT_TRANSACTION_CONTEXT.set(context)
    causal = _CURRENT_CAUSALITY.set((context.causal_ref(), context.root_transaction_id))
    try:
        yield context
    finally:
        _CURRENT_CAUSALITY.reset(causal)
        _CURRENT_TRANSACTION_CONTEXT.reset(token)


@contextmanager
def causal_scope(cause: CausalRef, root_transaction_id: TransactionId | None) -> Iterator[None]:
    """Set cause and root identifiers without opening a transaction."""
    token = _CURRENT_CAUSALITY.set((cause, root_transaction_id))
    try:
        yield
    finally:
        _CURRENT_CAUSALITY.reset(token)


@contextmanager
def continuation_callback() -> Iterator[None]:
    """Mark this with block as continuation work after a transaction result."""
    token = _continuation_depth.set(_continuation_depth.get() + 1)
    try:
        yield
    finally:
        _continuation_depth.reset(token)


def in_continuation() -> bool:
    return _continuation_depth.get() > 0
