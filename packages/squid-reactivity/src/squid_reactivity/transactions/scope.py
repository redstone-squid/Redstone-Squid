"""Open transactions and expose their identity, outcome, and callback registration.

Nested blocks share a handle. The engine owns staged changes and the commit boundary;
this module installs its scope and exposes the public handle.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from squid_reactivity.errors import (
    FreshTransactionError,
    StaleReactiveContextError,
)
from squid_reactivity.transactions.context import (
    ActorRef,
    Causality,
    TransactionContext,
    TransactionPurpose,
    TransactionRelation,
    causal_scope,
    current_causality,
)
from squid_reactivity.transactions.engine import (
    TransactionParticipant,
    _TransactionEngine,
    _write,
)
from squid_reactivity.transactions.patches import ConditionalCellPatch
from squid_reactivity.transactions.results import (
    TransactionCommit,
    TransactionResult,
    TransactionRollback,
)
from squid_reactivity.transactions.state import (
    _CURRENT_TRANSACTION,
    _active,
    _checkpoint,
    _current_task,
    _current_transaction,
)


class Transaction:
    """The identity and eventual outcome of one transaction.

    Nested blocks share this handle. Result is None until the outer block finishes,
    then remains available even if it raised. Hooks run after the outcome is fixed.
    """

    def __init__(self, context: TransactionContext, *, readonly: bool = False) -> None:
        self._readonly = readonly
        self._context = context
        self._result: TransactionResult | None = None

    @property
    def context(self) -> TransactionContext:
        return self._context

    @property
    def result(self) -> TransactionResult | None:
        return self._result

    def after_commit(self, callback: Callable[[TransactionCommit], None], *, key: object | None = None) -> None:
        """Run after commit; callback failures cannot change the committed result."""
        self._require_active_engine().add_commit_hook(callback, key=key)

    def after_rollback(self, callback: Callable[[TransactionRollback], None], *, key: object | None = None) -> None:
        """Run after rollback; callback failures cannot change the discarded result."""
        self._require_active_engine().add_rollback_hook(callback, key=key)

    def after_result(self, callback: Callable[[TransactionResult], None], *, key: object | None = None) -> None:
        """Run after either outcome. Further writes require a new transaction."""
        self._require_active_engine().add_result_hook(callback, key=key)

    @property
    def readonly(self) -> bool:
        """Whether this transaction rejects state writes."""
        return self._readonly

    def has_hook(self, key: object) -> bool:
        """Check whether this active transaction has a result callback registered under key."""
        return self._require_active_engine().has_hook(key)

    def _require_active_engine(self) -> _TransactionEngine:
        current = _current_transaction()
        if current is None or current.handle is not self:
            message = "callbacks must be registered inside their transaction"
            raise StaleReactiveContextError(message)
        return current


def current_transaction() -> Transaction | None:
    """Return the handle of the transaction this code is running in, or None outside one.

    The transaction counts from the start of its block until its outcome is decided.
    Result callbacks run after that and see None, as does a task that inherited the
    transaction and outlived it.
    """
    current = _active()
    return None if current is None else current.handle


@contextmanager
def _run_transaction(context: TransactionContext, *, readonly: bool = False) -> Iterator[Transaction]:
    """Install the transaction scope and hand its success or failure to the engine."""
    current = _TransactionEngine(Transaction(context, readonly=readonly), owner_task=_current_task())
    with causal_scope(context.causal_ref(), context.root_transaction_id):
        token = _CURRENT_TRANSACTION.set(current)
        try:
            _checkpoint("transaction.enter_body")
            yield current.handle
        except BaseException as error:
            _CURRENT_TRANSACTION.reset(token)
            current.rollback(error)
            raise
        _CURRENT_TRANSACTION.reset(token)
        current.finish()


@contextmanager
def transaction(
    name: str | None = None,
    *,
    actor: ActorRef | None = None,
    caused_by: TransactionContext | None = None,
    kind: TransactionPurpose = TransactionPurpose.NORMAL,
    relation: TransactionRelation | None = None,
    transaction_context: TransactionContext | None = None,
) -> Iterator[Transaction]:
    """Stage state changes and publish them together when the context exits.

    An exception in the body or preparation discards staged changes. Nested
    calls share the outer transaction; they are not independent savepoints.
    Only the task that opened the transaction may write through it. The yielded
    handle exposes the final result and registers outcome callbacks. Supply
    caused_by to link a new transaction to an earlier one. Identity arguments apply
    only when opening an outer transaction.

    Participants must not fail during apply, which runs after the commit point.
    Such a failure raises FrameworkIntegrityError and is recorded as a damaged
    commit, since already applied external changes cannot be rolled back here.
    """
    outer = _current_transaction()
    if outer is not None:
        # Checked at the `with`, not at the first write inside it, so a task that inherited
        # someone else's transaction is told where it went wrong rather than where it showed.
        if not outer.writable():
            outer.reject_stale("a nested transaction")
        yield outer.handle
        return
    explicit_identity = (
        name is not None
        or actor is not None
        or caused_by is not None
        or kind is not TransactionPurpose.NORMAL
        or relation is not None
    )
    if transaction_context is not None and explicit_identity:
        message = "supply either transaction_context or transaction identity arguments"
        raise ValueError(message)
    causality = (
        current_causality() if caused_by is None else Causality(caused_by.causal_ref(), caused_by.root_transaction_id)
    )
    context = transaction_context or TransactionContext.create(
        "transaction" if name is None else name,
        actor=actor,
        relation=relation,
        kind=kind,
        cause=None if causality is None else causality.cause,
        root_transaction_id=None if causality is None else causality.root_transaction_id,
    )
    with _run_transaction(context) as handle:
        yield handle


@contextmanager
def fresh_transaction(*, transaction_context: TransactionContext) -> Iterator[Transaction]:
    """Start an independent transaction while an enclosing transaction has staged no work.

    The enclosing scope is temporarily suspended and restored afterward. Reject an outer
    transaction with writes, participants, or version conditions: committing inner changes
    would otherwise make an outer rollback incomplete.

    UI undo/redo dispatch uses this to give the history operation its own identity inside
    an otherwise empty dispatch transaction.
    """
    outer = _current_transaction()
    if outer is not None and (outer.writes or outer.participants or outer.preconditions):
        message = "a fresh transaction cannot start after the admitting transaction staged changes"
        raise FreshTransactionError(message)
    with _CURRENT_TRANSACTION.set(None), transaction(transaction_context=transaction_context) as handle:
        yield handle


@contextmanager
def batch() -> Iterator[None]:
    """Group related writes into one change notification per model on commit."""
    with transaction():
        yield


@contextmanager
def readonly_transaction(*, transaction_context: TransactionContext | None = None) -> Iterator[Transaction]:
    """Run an identified read-only transaction and reject any state mutation."""
    if _current_transaction() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    causality = current_causality()
    context = transaction_context or TransactionContext.create(
        "read-only transaction",
        cause=None if causality is None else causality.cause,
        root_transaction_id=None if causality is None else causality.root_transaction_id,
    )
    with _run_transaction(context, readonly=True) as handle:
        yield handle


def enlist[ParticipantT: TransactionParticipant[Any]](
    key: object, factory: Callable[[], ParticipantT]
) -> ParticipantT | None:
    """Take part in the transaction in flight, staging writes instead of publishing them.

    Returns the participant registered for `key`, built by `factory` on first use, or
    `None` when no transaction is open -- the caller's signal that its write has nothing
    to wait for and should land now. `key` identifies the subsystem, is usually the
    subsystem itself, and must be hashable.
    """
    current = _current_transaction()
    if current is None:
        return None
    return current.enlist(key, factory)


def transaction_participant(key: object) -> TransactionParticipant[Any] | None:
    """Return the participant already registered for ``key``, without creating one."""
    current = _current_transaction()
    return None if current is None else current.participants.get(key)


def apply_conditional_patches(patches: Sequence[ConditionalCellPatch]) -> None:
    """Stage an all-or-nothing conditional patch set in the active transaction."""
    current = _current_transaction()
    if current is None or not current.writable():
        message = "conditional patches require an active writable transaction"
        raise RuntimeError(message)
    resolved = tuple((patch, *patch.target.resolve(patch.expected_version)) for patch in patches)
    for patch, _, cell in resolved:
        current.require_version(cell, patch.expected_version)
    for patch, owner, cell in resolved:
        _write(owner, patch.target.name, cell, patch.value.raw())
