"""Transactional state and computed values for component trees.

Each state field has a cell containing a value and a version. Assigning state
stages a replacement in the current transaction, or publishes it immediately
when no transaction is open. Callers must replace ordinary values rather than
mutate them in place; snapshots retain references instead of copying values.

Computed values record their dependencies and check versions when read. Sources
do not retain their readers, so a long-lived shared field does not keep every
component that once read it alive.
"""

import asyncio
import functools
import inspect
import logging
import threading
import uuid
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol, Self, overload, override

from squid_reactivity.actions import (
    ActionCommit,
    ActionContext,
    ActionContinuation,
    ActionPurpose,
    ActionResult,
    ActionRollback,
    ActorRef,
    ChangeReport,
    ConflictDetail,
    ExceptionReport,
    ObservedRead,
    RollbackReason,
    TransactionContribution,
    action_scope,
    continuation_callback,
    current_action,
    current_causality,
    emit_continuation_failure,
    emit_result,
    in_continuation,
    next_commit_sequence,
)
from squid_reactivity.addresses import CellAddress, publish

_log = logging.getLogger(__name__)

_MISSING = object()
"""A cell that has never been assigned. Distinct from ``None``, which is a real value."""


class ReactiveOwner(Protocol):
    """Hooks called when an owner's state commits or rolls back.

    State descriptors also require an instance dictionary for cell storage.
    """

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


class TransactionView(Protocol):
    """Read-only access to the frozen overlay during participant preparation."""

    def read_staged(self, target: CellTarget) -> SlotValue: ...

    def read_committed(self, target: CellTarget) -> SlotValue: ...

    @property
    def context(self) -> ActionContext: ...


class TransactionParticipant[PreparedT](Protocol):
    """Join an action with writes managed outside the state cells.

    Register through enlist. During commit, prepare validates the staged changes
    and returns the data apply needs. Preparation must not publish changes. If it
    fails, the action aborts before publication.

    Apply runs synchronously after the commit point and must not fail. Finalize
    runs after all participants have applied, when subscribers can read the full
    result. A finalize failure is reported without undoing the commit.
    """

    def prepare(self, view: TransactionView) -> PreparedT:
        """Validate the staged writes and return what `apply` needs, publishing none of them.

        Raise to abort the action: every participant is aborted, component state is
        restored, and the error reaches whoever called the handler.
        """
        ...

    def apply(self, prepared: PreparedT) -> None:
        """Publish the prepared change synchronously. This method must not raise."""

    def describe_change(self, prepared: PreparedT) -> TransactionContribution | None:
        """Return the participant's reversible token, or no history contribution."""

    def abort(self, prepared: PreparedT | None, cause: BaseException) -> None:
        """Discard staged work after failure. Called once in reverse participant order."""

    def finalize(self, prepared: PreparedT) -> None:
        """Notify subscribers after all participants have applied.

        An exception here is reported but does not roll back the committed action.
        """


class ReactivityError(Exception):
    """Base class for the package's named reactive-state errors.

    Subclasses also inherit standard exception types such as RuntimeError,
    ValueError, or LookupError so callers can catch either category.
    """


class ReactiveWriteError(ReactivityError, RuntimeError):
    """A state mutation was attempted inside a read-only action."""


class ActionValidationError(ReactivityError, ValueError):
    """An admitted action failed application validation before publication."""


class FrameworkIntegrityError(ReactivityError, RuntimeError):
    """An infallible publication adapter failed after the local commit point."""


class FreshActionError(ReactivityError, RuntimeError):
    """A distinct action was requested after its admitting transaction staged work."""


class UndeclaredStateError(ReactivityError, RuntimeError):
    """An attribute that is not declared state was written inside a transaction."""


class ReactiveCycleError(ReactivityError, RuntimeError):
    """A computed value or resource depends on itself, directly or indirectly.

    The path includes the full cycle, for example ``a -> b -> a``. Dependencies
    can cross between computed values and asynchronous resource loaders.
    """

    def __init__(self, path: Sequence[str]) -> None:
        self.path = tuple(path)
        joined = " -> ".join(self.path)
        message = f"cycle: {joined}. Nothing in that ring can settle, because each waits on the next."
        super().__init__(message)


class StaleReactiveContextError(ReactivityError, RuntimeError):
    """A write used a finished transaction or came from a different task.

    Child tasks inherit the transaction context, but only its owning task may
    stage writes. Reads from child tasks are allowed. Once the transaction closes,
    reads ignore its staged values and use the committed cells.
    """


class ReactiveConflictError(ReactivityError, RuntimeError):
    """A required shared-state version changed before commit.

    The action discards its staged changes. The error is raised when the
    transaction context exits, so catch it outside the with block.
    """

    def __init__(self, detail: ConflictDetail, message: str) -> None:
        self.detail = detail
        super().__init__(message)


def _equal(left: object, right: object) -> bool:
    """Compare values, treating an equality-check failure as a difference."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


def _frozen(value: Any) -> Any:
    """Return a restored snapshot value in the shape it was exported from.

    JSON has one sequence type, so an exported tuple comes back as a list. A field declared
    `Sequence` reads either; one declared `tuple[...]` needs the tuple back.
    """
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_reactivity_transaction", default=None)
_RELAXED_READS: ContextVar[int] = ContextVar("squid_reactivity_relaxed_reads", default=0)
_STRONG_READS: ContextVar[int] = ContextVar("squid_reactivity_strong_reads", default=0)
_INTERLEAVER: ContextVar[Callable[[str], None] | None] = ContextVar(
    "squid_reactivity_deterministic_interleaver", default=None
)
_INTERLEAVER_USERS: int = 0


def _checkpoint(name: str) -> None:
    if _INTERLEAVER_USERS == 0:
        return
    interleaver = _INTERLEAVER.get()
    if interleaver is not None:
        interleaver(name)


def _current_task() -> object | None:
    """Return the current asyncio task, or None when no event loop is running."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _active() -> _Transaction | None:
    """Return the open transaction used for reads, ignoring a finished context.

    A child task may retain a transaction after its context exits. Reads then
    use committed state, while writes through that context are rejected.
    """
    current = _CURRENT.get()
    return None if current is None or current.closed else current


class _Cell:
    """Store one field's value, version, and optional publication address.

    Reactive fields, including component fields, have CellAddress values. Topics
    use cells with Topic addresses and no payload. Addressed reads can
    be checked for conflicts and collected as render subscriptions.
    """

    __slots__ = ("__weakref__", "address", "identity", "value", "version")

    def __init__(self, value: Any = _MISSING, version: int = 0, address: Any = None) -> None:
        self.identity = uuid.uuid7()
        self.value = value
        self.version = version
        self.address = address

    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    def settle(self) -> int:
        """Return the version a reader should compare against. A cell is always settled."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                return entry.version
        return self.version

    def track(self, version: int) -> None:
        """Record a read of this cell at `version` with whatever is consuming it."""
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = version

    def read(self) -> Any:
        """Return the value this reader should see, staged write included, and record it."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                # A staged read does not observe committed state, so it adds no
                # commit-time version precondition.
                self.track(entry.version)
                return entry.value
            if self.address is not None and _RELAXED_READS.get() == 0:
                current.observe(self, strong=_STRONG_READS.get() > 0)
        self.track(self.version)
        return self.value

    def write(self, value: Any) -> None:
        self.value = value
        self.version += 1
        _bump_epoch()

    def touch(self) -> None:
        """Advance the version without replacing the value."""
        self.version += 1
        _bump_epoch()

    def restore(self, value: Any, version: int) -> None:
        """Put a cell back exactly as an action found it, version included.

        The version goes back rather than forward: a reader that sampled this cell before the
        action is still valid, and bumping would make it recompute for a value that never
        changed. The epoch still moves, because settled readers must re-check.
        """
        self.value = value
        self.version = version
        _bump_epoch()


def _staged_value(cell: _Cell) -> Any:
    """This action's pending value for `cell`, or `_MISSING` if it has not written one."""
    current = _active()
    if current is None:
        return _MISSING
    entry = current.staged(cell)
    return _MISSING if entry is None else entry.value


class _ReactiveSource(Protocol):
    """A versioned value with explicit upstream dependencies and a notification address.

    Settling may change the upstream graph without changing the value's version.
    Address traversal therefore follows the live graph after validation.
    """

    @property
    def address(self) -> Any: ...

    def dependencies(self) -> Iterable[_ReactiveSource]: ...

    def settle(self) -> int: ...


class _Consumer(Protocol):
    """Collect dependencies read by a computed value, resource load, or render."""

    sources: dict[_ReactiveSource, int]


_CONSUMER: ContextVar[_Consumer | None] = ContextVar("squid_reactivity_consumer", default=None)


class _Signal:
    """Observe framework-managed changes that do not belong to an action transaction.

    Operation progress and history bookkeeping publish immediately. Keeping their
    versions in the ordinary dependency graph makes computed values and renders
    follow them without assigning either a component owner.
    """

    def __init__(self) -> None:
        self.address = CellAddress(self, "revision")
        self.version = 0

    def track(self) -> None:
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version

    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    def settle(self) -> int:
        return self.version

    def notify(self) -> None:
        self.version += 1
        _bump_epoch()
        publish(self.address)


class _SettlingNode(Protocol):
    """A named derived value that can participate in cycle detection."""

    _label: str


_SETTLING: ContextVar[tuple[_SettlingNode, ...]] = ContextVar("squid_reactivity_settling", default=())
"""The derived nodes currently producing a value on this task, outermost first.

Task-local rather than global: two independent values settled concurrently each copy the
context, so one cannot be mistaken for the other's dependency. A computed and a resource share
one stack because a chain can run through both, and a cycle should be named whole.
"""


def cycle_path(node: _SettlingNode) -> tuple[str, ...] | None:
    """Return the cycle ending at ``node``, or None if it is not already active.

    The path starts at the first occurrence of the node and repeats it at the
    end. Callers that led into the cycle are excluded from the diagnostic.
    """
    stack = _SETTLING.get()
    for index, entered in enumerate(stack):
        if entered is node:
            return (*(held._label for held in stack[index:]), node._label)
    return None


@contextmanager
def settling(node: _SettlingNode) -> Iterator[None]:
    """Track a computation or load, raising before it re-enters an active dependency."""
    path = cycle_path(node)
    if path is not None:
        raise ReactiveCycleError(path)
    token = _SETTLING.set((*_SETTLING.get(), node))
    try:
        yield
    finally:
        _SETTLING.reset(token)


_READ_ONLY: ContextVar[object | None] = ContextVar("squid_reactivity_read_only", default=None)


@contextmanager
def read_only() -> Iterator[None]:
    """Reject reactive writes, except initialization of objects created in this scope.

    Nested scopes cannot initialize objects belonging to an outer scope. Dependency
    capture is independent: use capture_reads() to collect reads as well.
    """
    token = _READ_ONLY.set(object())
    try:
        yield
    finally:
        _READ_ONLY.reset(token)


def _check_write(owner: ReactiveOwner, name: str) -> None:
    scope = _READ_ONLY.get()
    if scope is not None and owner.__dict__.get("_reactive_initialization") is not scope:
        message = (
            f"{type(owner).__name__}.{name} was written during a read-only computation. "
            "Initialize defaults with state(factory=...), derive values with computed, "
            "and update existing state outside the computation."
        )
        raise ReactiveWriteError(message)


_EPOCH = 0
"""Advance when state is staged, published, restored, or invalidated.

Computed values use this counter to skip dependency checks when no state has changed.
"""


def _bump_epoch() -> None:
    global _EPOCH
    _EPOCH += 1


@contextmanager
def untracked() -> Iterator[None]:
    """Read state without subscribing to it.

    For code that reads to decide what to do rather than to derive a value from it. Action
    handlers already run outside any consumer, so this is for a computed that deliberately
    samples something it does not want to recompute for.
    """
    token = _CONSUMER.set(None)
    try:
        yield
    finally:
        _CONSUMER.reset(token)


@contextmanager
def relaxed_read() -> Iterator[None]:
    """Read reactive state without adding a commit-time consistency precondition.

    Only reads a precondition would otherwise cover are affected: a cell this action also
    writes, and any read taken inside :func:`strong_read`. Unlike :func:`untracked`, this
    does not change reactive dependency tracking.
    """
    token = _RELAXED_READS.set(_RELAXED_READS.get() + 1)
    try:
        yield
    finally:
        _RELAXED_READS.reset(token)


@contextmanager
def strong_read() -> Iterator[None]:
    """Require every reactive read taken inside to still hold its version at commit.

    The upgrade an action opts into when it branches on reactive state it does not write --
    read A, write B, and by default nothing says A was still true. Wrap the whole handler
    body to make one action serializable over what it read; wrap one read to guard just that
    one. :func:`relaxed_read` nested inside opts a read back out.
    """
    token = _STRONG_READS.set(_STRONG_READS.get() + 1)
    try:
        yield
    finally:
        _STRONG_READS.reset(token)


@dataclass(slots=True)
class _Staged:
    """A pending cell replacement and the value and version that preceded it.

    Before publication, rollback discards this entry without changing the cell.
    Values are retained by reference, so ordinary state must be treated as immutable.
    """

    owner: ReactiveOwner
    name: str
    cell: _Cell
    before: Any
    before_version: int
    value: Any
    version: int


@dataclass(frozen=True, slots=True)
class SlotValue:
    """A state slot value that distinguishes absence from a present ``None``."""

    present: bool
    value: Any = None

    @classmethod
    def from_raw(cls, value: Any) -> SlotValue:
        return cls(value is not _MISSING, None if value is _MISSING else value)

    def raw(self) -> Any:
        return self.value if self.present else _MISSING


def _slot_name(cell: _Cell) -> str:
    """Return the shared field address, or an opaque slot ID for local state."""
    return f"slot:{cell.identity}" if cell.address is None else str(cell.address)


@dataclass(frozen=True, slots=True, init=False)
class CellTarget:
    """Refer to a reversible state slot without keeping its owner alive.

    The opaque identity remains available after collection. History can therefore
    report which target expired even when the cell and its readable address are gone.
    """

    _owner: weakref.ReferenceType[ReactiveOwner]
    _cell: weakref.ReferenceType[_Cell]
    name: str
    identity: str

    def __init__(self, owner: ReactiveOwner, name: str, cell: _Cell) -> None:
        object.__setattr__(self, "_owner", weakref.ref(owner))
        object.__setattr__(self, "_cell", weakref.ref(cell))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "identity", f"slot:{cell.identity}")

    def resolve(self, expected_version: int) -> tuple[ReactiveOwner, _Cell]:
        """Return the live slot or report an expired conditional inverse."""
        owner = self._owner()
        cell = self._cell()
        if owner is None or cell is None:
            detail = ConflictDetail(self.identity, expected_version, -1)
            raise ReactiveConflictError(detail, f"{self.identity} no longer exists; nothing was published")
        return owner, cell

    def live_cell(self) -> _Cell | None:
        """Return the slot for non-mutating diagnostics, or ``None`` after collection."""
        return self._cell()


@dataclass(frozen=True, slots=True)
class CellPatch:
    """One state replacement with exact before/after version lineage."""

    target: CellTarget
    before: SlotValue
    after: SlotValue
    before_version: int
    after_version: int

    def inverse(self) -> ConditionalCellPatch:
        return ConditionalCellPatch(self.target, self.before, self.after_version)


@dataclass(frozen=True, slots=True)
class ConditionalCellPatch:
    """A replacement applicable only while the target has the expected version."""

    target: CellTarget
    value: SlotValue
    expected_version: int


@dataclass(frozen=True, slots=True)
class CellPatchSet:
    """Immutable physical changes from one committed action."""

    patches: tuple[CellPatch, ...] = ()

    def __iter__(self) -> Iterator[CellPatch]:
        return iter(self.patches)

    def __len__(self) -> int:
        return len(self.patches)

    def addresses(self) -> tuple[Any, ...]:
        found: list[Any] = []
        seen: set[int] = set()
        for patch in self.patches:
            cell = patch.target.live_cell()
            address = None if cell is None else cell.address
            if address is None or id(address) in seen:
                continue
            seen.add(id(address))
            found.append(address)
        return tuple(found)

    def inverse(self) -> tuple[ConditionalCellPatch, ...]:
        return tuple(patch.inverse() for patch in reversed(self.patches))


def apply_conditional_patches(patches: Sequence[ConditionalCellPatch]) -> None:
    """Stage an all-or-nothing conditional patch set in the active transaction."""
    current = _CURRENT.get()
    if current is None or not current.writable():
        message = "conditional patches require an active writable transaction"
        raise RuntimeError(message)
    resolved = tuple((patch, *patch.target.resolve(patch.expected_version)) for patch in patches)
    for patch, _, cell in resolved:
        current.require_version(cell, patch.expected_version)
    for patch, owner, cell in resolved:
        _write(owner, patch.target.name, cell, patch.value.raw())


def _write(owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
    """Stage a write into the action in flight, or land it now if there is none.

    `enlist` already returns `None` when no transaction is open; this is the same
    signal read directly. A component built during the action is not protected by it, so
    its construction lands immediately too.
    """
    _check_write(owner, name)
    current = _CURRENT.get()
    if current is None and in_continuation():
        message = "direct reactive mutation in an continuation hook is forbidden; use continuation.start_action(...)"
        raise ReactiveWriteError(message)
    if current is not None and not current.writable():
        current.reject_stale(f"{type(owner).__name__}.{name}")
    if current is None or not current.protects(owner):
        cell.write(value)
        owner._state_changed(frozenset((name,)))
        return
    current.stage(owner, name, cell, value)


class Transaction:
    """The identity and eventual outcome of one transaction.

    Nested blocks share this handle. Result is None until the outer block finishes,
    then remains available even if it raised. Hooks run after the outcome is fixed.
    """

    def __init__(self, context: ActionContext) -> None:
        self._context = context
        self._result: ActionResult | None = None

    @property
    def context(self) -> ActionContext:
        return self._context

    @property
    def result(self) -> ActionResult | None:
        return self._result

    def after_commit(self, callback: Callable[[ActionCommit], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_commit(lambda result, continuation: callback(result), key=key)

    def after_rollback(self, callback: Callable[[ActionRollback], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_rollback(lambda result, continuation: callback(result), key=key)

    def after_result(self, callback: Callable[[ActionResult], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_result(lambda result, continuation: callback(result), key=key)

    def _check_current(self) -> None:
        current = _CURRENT.get()
        if current is None or current.handle is not self:
            message = "callbacks must be registered inside their transaction"
            raise StaleReactiveContextError(message)


@dataclass(slots=True)
class _Transaction:
    readonly: bool = False
    closed: bool = False
    """Whether this transaction has finished, successfully or not.

    Set once the commit or the rollback is done, so a task still holding an inherited copy
    finds out rather than staging into an action nobody will publish.
    """
    owner_task: object | None = None
    """The task that opened this transaction, or `None` outside a running loop."""
    writes: dict[_Cell, _Staged] = field(default_factory=dict)
    observed: dict[_Cell, int] = field(default_factory=dict)
    """Committed versions this action read out of addressed cells, first read per cell.

    Only shared cells are recorded, because only they can move under a running action.
    Recorded whether or not the read carries a precondition, because the ledger reports what
    an action looked at and that is true either way.
    """
    strong: dict[_Cell, int] = field(default_factory=dict)
    """The subset of `observed` taken inside `strong_read()`, which must still hold at commit."""
    preconditions: dict[_Cell, int] = field(default_factory=dict)
    changed: dict[int, ReactiveOwner] = field(default_factory=dict)
    changed_names: dict[int, set[str]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    commit_hooks: list[tuple[object | None, Callable[[ActionCommit, ActionContinuation], None]]] = field(
        default_factory=list
    )
    rollback_hooks: list[tuple[object | None, Callable[[ActionRollback, ActionContinuation], None]]] = field(
        default_factory=list
    )
    result_hooks: list[tuple[object | None, Callable[[ActionResult, ActionContinuation], None]]] = field(
        default_factory=list
    )
    context: ActionContext = field(default_factory=ActionContext.create)
    participants: dict[object, TransactionParticipant[Any]] = field(default_factory=dict)
    applied: bool = False
    """Whether `publish` has put the staged values into the cells.

    Rollback before this point must not write: the cells were never touched, and a shared
    cell may hold a value someone else committed while this action was staging. Restoring
    `before` over that would revert a write this action never saw.
    """
    published: bool = False
    """Whether the commit reached the point where this action's writes became visible."""
    write_block: str | None = None
    """Why state may not be written right now, while the transaction itself stays open."""
    aborted: bool = False
    cleanup_errors: list[ExceptionReport] = field(default_factory=list)
    prepared_participants: tuple[tuple[TransactionParticipant[Any], Any], ...] = ()
    commit_record: ActionCommit | None = None
    handle: Transaction = field(init=False)

    def __post_init__(self) -> None:
        self.handle = Transaction(self.context)

    def note_born(self, owner: object) -> None:
        """Record an object created after this transaction began."""
        self.born[id(owner)] = owner

    def writable(self) -> bool:
        """Whether this transaction is open and the caller owns it.

        Read-only and temporary write restrictions are checked separately by stage.
        """
        return not self.closed and (self.owner_task is None or self.owner_task is _current_task())

    def reject_stale(self, what: str) -> None:
        """Raise an error identifying the finished transaction or wrong task."""
        if self.closed:
            message = (
                f"{what} was written through a transaction that has already finished. The write "
                f"came from a task that outlived the action which opened it, so nothing would "
                f"publish it: give the task an owner inside the handler, or write in the handler."
            )
        else:
            message = (
                f"{what} was written from a task other than the one that opened its transaction. "
                f"Sibling tasks staging into one overlay make what commits depend on scheduling: "
                f"write from the handler's own task, or give each branch its own transaction."
            )
        raise StaleReactiveContextError(message)

    def protects(self, owner: object) -> bool:
        """Whether this transaction is responsible for the owner's state.

        A transaction restores the view the action started from. An object created during the
        action had no state then, so writing to it is construction, not mutation.
        """
        return id(owner) not in self.born

    def staged(self, cell: _Cell) -> _Staged | None:
        """This action's pending write to `cell`, if it has made one."""
        return self.writes.get(cell)

    def observe(self, cell: _Cell, *, strong: bool) -> None:
        """Record the first committed version read from a shared cell.

        Strong reads have a separate first-read version. An earlier ordinary read
        must not become a precondition just because a later read is strong.
        """
        if cell not in self.observed:
            self.observed[cell] = cell.version
        if strong and cell not in self.strong:
            self.strong[cell] = cell.version

    def check_preconditions(self) -> None:
        """Check required versions before publishing writes or participant changes.

        Reads of cells this action also writes are checked automatically. Strong
        reads and explicit patch preconditions are checked even when their cells
        are not written. Other reads are recorded for diagnostics only. Blind writes
        have no read precondition, so the last commit wins.
        """
        _checkpoint("commit.before_validation")
        if not (self.writes or self.participants):
            _checkpoint("commit.after_validation")
            return
        required = {cell: version for cell, version in self.observed.items() if cell in self.writes}
        required.update(self.strong)
        required.update(self.preconditions)
        for cell, version in required.items():
            if cell.version == version:
                continue
            detail = ConflictDetail(self.target_id(cell), version, cell.version)
            message = (
                f"{_slot_name(cell)} changed while this action was running: expected version "
                f"{version}, found {cell.version}. Nothing was published."
            )
            raise ReactiveConflictError(detail, message)
        _checkpoint("commit.after_validation")

    def target_id(self, cell: _Cell) -> str:
        return _slot_name(cell)

    def require_version(self, cell: _Cell, version: int) -> None:
        existing = self.preconditions.get(cell)
        if existing is not None and existing != version:
            detail = ConflictDetail(self.target_id(cell), existing, version)
            raise ReactiveConflictError(detail, "incompatible inverse preconditions")
        self.preconditions[cell] = version

    def stage(self, owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
        """Hold a write for commit, visible only to readers of this transaction."""
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot mutate component state"
            raise ReactiveWriteError(message)
        entry = self.writes.get(cell)
        if entry is None:
            self.writes[cell] = _Staged(owner, name, cell, cell.value, cell.version, value, cell.version + 1)
        else:
            entry.value = value
            entry.version += 1
        self.changed[id(owner)] = owner
        self.changed_names.setdefault(id(owner), set()).add(name)
        # The staged version is what a read inside this action reports, so a computed that
        # sampled it has to be told the world moved even though nothing is published yet.
        _bump_epoch()

    def publish(self) -> None:
        """Install staged values and advance each cell beyond its committed version.

        Keep the staged version when it is still newer than the committed one. If
        another action committed first, advance beyond that version instead. Reusing
        a version would leave computed values unable to detect the new value.
        """
        if not self.writes:
            self.applied = True
            return
        for entry in self.writes.values():
            entry.cell.value = entry.value
            entry.cell.version = max(entry.version, entry.cell.version + 1)
        self.applied = True
        _bump_epoch()

    def restore(self) -> None:
        """Put every written cell back where the action found it.

        Only if the values ever left the overlay. An action that fails before publication --
        every handler failure, and every rejected commit precondition -- never touched a cell,
        so there is nothing to put back and writing anyway would clobber whoever did.
        """
        for entry in reversed(tuple(self.writes.values())):
            if self.applied:
                entry.cell.restore(entry.before, entry.before_version)
            entry.owner._state_rolled_back()
        if self.writes:
            # Even when nothing was written back: a computed that read a staged version is
            # settled against a version that has just stopped existing, and only the epoch
            # tells it to look again.
            _bump_epoch()

    def enlist[ParticipantT: TransactionParticipant[Any]](
        self, key: object, factory: Callable[[], ParticipantT]
    ) -> ParticipantT:
        """Return this action's participant for `key`, creating it on first use.

        The guards run on every call, not just the first: a participant enlisted before a
        `block_writes` region may not keep staging writes inside one.
        """
        if not self.writable():
            self.reject_stale("a transaction participant")
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot stage writes"
            raise ReactiveWriteError(message)
        existing = self.participants.get(key)
        if existing is not None:
            return existing  # type: ignore[bad-return]
        created = factory()
        self.participants[key] = created
        return created

    def patches(self) -> CellPatchSet:
        """Freeze the physical slot lineage written by this action."""
        if not self.writes:
            return CellPatchSet()
        return CellPatchSet(
            tuple(
                CellPatch(
                    CellTarget(entry.owner, entry.name, entry.cell),
                    SlotValue.from_raw(entry.before),
                    SlotValue.from_raw(entry.value),
                    entry.before_version,
                    entry.version,
                )
                for entry in self.writes.values()
            )
        )

    def reads(self) -> tuple[ObservedRead, ...]:
        return tuple(ObservedRead(self.target_id(cell), version) for cell, version in self.observed.items())

    @contextmanager
    def preparing(self) -> Iterator[None]:
        """Expose staged values to participants while preventing further writes.

        The transaction context has already been removed before commit. Restore it
        for preparation so participants can read the values they are validating.
        Block writes and new participants, and exclude these internal reads from
        the observation report because conflict validation has already run.
        """
        token = _CURRENT.set(self)
        try:
            with block_writes("a transaction participant cannot write while preparing"), relaxed_read():
                yield
        finally:
            _CURRENT.reset(token)

    def commit(self) -> ActionCommit:
        """Prepare everything, publish synchronously, and return the immutable commit."""
        self.check_preconditions()
        view = _FrozenTransactionView(self)
        prepared: list[tuple[TransactionParticipant[Any], Any]] = []
        try:
            with self.preparing():
                for participant in self.participants.values():
                    _checkpoint("commit.before_participant_prepare")
                    prepared.append((participant, participant.prepare(view)))
                    _checkpoint("commit.after_participant_prepare")
        except BaseException as error:
            self.abort(error, prepared)
            raise
        self.prepared_participants = tuple(prepared)
        changes = tuple(
            change for participant, value in prepared if (change := participant.describe_change(value)) is not None
        )
        committed_at = datetime.now(UTC)
        commit = ActionCommit(
            self.context,
            next_commit_sequence(),
            committed_at,
            committed_at - self.context.started_at,
            self.reads(),
            self.patches(),
            changes,
        )
        self.commit_record = commit
        _checkpoint("commit.before_publication")
        self.publish()
        _checkpoint("commit.after_cell_publication")
        # Cell installation begins the infallible publication phase. An adapter that raises
        # from apply has violated the participant contract; rolling cells back could not undo
        # a participant that applied only partly, so preserve the committed diagnostic truth.
        self.published = True
        try:
            for participant, value in prepared:
                participant.apply(value)
                _checkpoint("commit.after_participant_apply")
        except BaseException:
            _log.critical("an infallible transaction participant apply failed", exc_info=True)
            raise

        return commit

    def finalize_commit(self) -> None:
        """Notify reactive consumers after the commit gate, isolating continuation failures."""
        assert self.commit_record is not None
        if not self.changed and not self.prepared_participants:
            return
        _checkpoint("continuation.before_notification")
        for owner in self.changed.values():
            try:
                owner._state_changed(frozenset(self.changed_names[id(owner)]))
            except Exception as error:
                _log.exception("a reactive owner failed to process its committed state")
                emit_continuation_failure(
                    self.commit_record,
                    "notification",
                    f"{type(owner).__qualname__}._state_changed",
                    error,
                )
        for participant, value in self.prepared_participants:
            _checkpoint("continuation.before_participant_finalize")
            try:
                participant.finalize(value)
            except Exception as error:
                _log.exception("a transaction participant failed to finalize")
                emit_continuation_failure(
                    self.commit_record,
                    "participant_finalize",
                    f"{type(participant).__qualname__}.finalize",
                    error,
                )

    def abort(
        self,
        cause: BaseException,
        prepared: Sequence[tuple[TransactionParticipant[Any], Any]] | None = None,
    ) -> tuple[ExceptionReport, ...]:
        if self.aborted:
            return tuple(self.cleanup_errors)
        self.aborted = True
        _checkpoint("rollback.before_abort")
        values = {
            id(participant): value
            for participant, value in (self.prepared_participants if prepared is None else prepared)
        }
        failures: list[ExceptionReport] = []
        for participant in reversed(tuple(self.participants.values())):
            try:
                participant.abort(values.get(id(participant)), cause)
            except Exception as error:
                report = ExceptionReport.capture(error)
                failures.append(report)
                self.cleanup_errors.append(report)
                _log.exception("a transaction participant failed to abort")
        self.restore()
        return tuple(failures)


@dataclass(frozen=True, slots=True)
class _FrozenTransactionView:
    """The immutable overlay exposed only during participant preparation."""

    transaction: _Transaction

    @property
    def context(self) -> ActionContext:
        return self.transaction.context

    def read_staged(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        staged = self.transaction.staged(cell)
        return SlotValue.from_raw(cell.value if staged is None else staged.value)

    def read_committed(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        return SlotValue.from_raw(cell.value)


def report_undeclared_write(owner: object, name: str) -> None:
    """Reject writes to undeclared attributes during a transaction.

    Those writes cannot be rolled back or trigger reactive updates. Objects
    created inside the transaction are exempt so their constructors can run.
    """
    current = _active()
    if current is None or not current.protects(owner):
        return
    label = f"{type(owner).__name__}.{name}"
    if current.write_block is not None:
        message = f"{current.write_block} ({label})"
        raise ReactiveWriteError(message)
    if current.readonly:
        message = f"parallel-read actions cannot mutate component state ({label})"
        raise ReactiveWriteError(message)
    message = (
        f"{label} was assigned inside a transaction but is not declared state: it would not be "
        f"rolled back if the action failed, and it would not trigger a re-render. "
        f"Declare it with state()."
    )
    raise UndeclaredStateError(message)


_COMMIT_GATE = threading.RLock()


def _rollback_reason(error: BaseException, *, during_commit: bool) -> RollbackReason:
    if isinstance(error, asyncio.CancelledError):
        return RollbackReason.CANCELLED
    if isinstance(error, ReactiveConflictError):
        return RollbackReason.CONFLICT
    if isinstance(error, ActionValidationError):
        return RollbackReason.VALIDATION_FAILED
    if during_commit:
        return RollbackReason.PARTICIPANT_PREPARE_FAILED
    return RollbackReason.HANDLER_EXCEPTION


def _notify_hooks(
    result: ActionResult,
    hooks: Sequence[tuple[object | None, Callable[[Any, ActionContinuation], None]]],
) -> None:
    if not hooks:
        return
    continuation = ActionContinuation(result)
    with continuation_callback():
        for _, callback in hooks:
            _checkpoint("continuation.before_hook")
            try:
                callback_result = callback(result, continuation)
                if inspect.isawaitable(callback_result):
                    if inspect.iscoroutine(callback_result):
                        callback_result.close()
                    message = "action continuation hooks must be synchronous; start an operation instead"
                    raise TypeError(message)  # noqa: TRY301
            except Exception as error:
                _log.exception("an action continuation hook failed")
                emit_continuation_failure(
                    result,
                    "hook",
                    getattr(callback, "__qualname__", type(callback).__qualname__),
                    error,
                )


def _emit_commit(current: _Transaction, commit: ActionCommit) -> None:
    current.handle._result = commit
    emit_result(commit)
    _notify_hooks(commit, current.commit_hooks)
    _notify_hooks(commit, current.result_hooks)


def _emit_rollback(current: _Transaction, error: BaseException, *, during_commit: bool) -> ActionRollback:
    cleanup_errors = current.abort(error)
    rolled_back_at = datetime.now(UTC)
    rollback = ActionRollback(
        current.context,
        rolled_back_at,
        rolled_back_at - current.context.started_at,
        _rollback_reason(error, during_commit=during_commit),
        current.reads(),
        error.detail if isinstance(error, ReactiveConflictError) else None,
        ExceptionReport.capture(error),
        ChangeReport(len(current.writes), len(current.participants)),
        cleanup_errors,
    )
    current.handle._result = rollback
    emit_result(rollback)
    _notify_hooks(rollback, current.rollback_hooks)
    _notify_hooks(rollback, current.result_hooks)
    return rollback


@contextmanager
def transaction(
    name: str | None = None,
    *,
    actor: ActorRef | None = None,
    caused_by: ActionContext | None = None,
    kind: ActionPurpose = ActionPurpose.NORMAL,
    action_context: ActionContext | None = None,
) -> Iterator[Transaction]:
    """Stage state changes and publish them together when the context exits.

    An exception in the body or preparation discards staged changes. Nested
    calls share the outer transaction; they are not independent savepoints.
    Only the task that opened the transaction may write through it. The yielded
    handle exposes the final result and registers outcome callbacks. Supply
    caused_by to link a new action to an earlier one. Identity arguments apply
    only when opening an outer transaction.

    Participants must not fail during apply, which runs after the commit point.
    Such a failure raises FrameworkIntegrityError and is recorded as a damaged
    commit, since already applied external changes cannot be rolled back here.
    """
    outer = _CURRENT.get()
    if outer is not None:
        # Checked at the `with`, not at the first write inside it, so a task that inherited
        # someone else's transaction is told where it went wrong rather than where it showed.
        if not outer.writable():
            outer.reject_stale("a nested transaction")
        yield outer.handle
        return
    explicit_identity = (
        name is not None or actor is not None or caused_by is not None or kind is not ActionPurpose.NORMAL
    )
    if action_context is not None and explicit_identity:
        message = "supply either action_context or transaction identity arguments"
        raise ValueError(message)
    causality = current_causality() if caused_by is None else (caused_by.causal_ref(), caused_by.root_action_id)
    context = (
        action_context
        or (current_action() if not explicit_identity and not in_continuation() else None)
        or ActionContext.create(
            "action" if name is None else name,
            actor=actor,
            kind=kind,
            cause=None if causality is None else causality[0],
            root_action_id=None if causality is None else causality[1],
        )
    )
    current = _Transaction(owner_task=_current_task(), context=context)
    with action_scope(context):
        token = _CURRENT.set(current)
        try:
            _checkpoint("transaction.enter_body")
            yield current.handle
        except BaseException as error:
            _CURRENT.reset(token)
            current.closed = True
            _emit_rollback(current, error, during_commit=False)
            raise
        _CURRENT.reset(token)
        try:
            _checkpoint("transaction.close_staging")
            with _COMMIT_GATE:
                commit = current.commit()
        except BaseException as error:
            current.closed = True
            if current.published:
                # Publication is the commit point. An apply failure is an adapter integrity
                # defect, not a user rollback; retain the commit truth and fail loudly.
                assert current.commit_record is not None
                integrity_commit = replace(
                    current.commit_record,
                    tags=frozenset({RollbackReason.FRAMEWORK_INTEGRITY_FAILURE.value}),
                )
                current.handle._result = integrity_commit
                emit_result(integrity_commit)
                message = f"an infallible transaction adapter failed after the commit point: {error}"
                raise FrameworkIntegrityError(message) from error
            else:
                _emit_rollback(current, error, during_commit=True)
            raise
        _checkpoint("commit.gate_released")
        current.finalize_commit()
        current.closed = True
        _emit_commit(current, commit)


@contextmanager
def fresh_action_transaction(*, action_context: ActionContext) -> Iterator[Transaction]:
    """Start a distinct causal transaction while an empty admitting transaction is open.

    Mounted undo/redo handlers are admitted inside the dispatch transaction. Suspending that
    empty envelope lets the history operation remain a real action with its own identity. An
    outer transaction that already staged work is rejected because committing the inner action
    could not then be rolled back with the outer one.
    """
    outer = _CURRENT.get()
    if outer is not None and (outer.writes or outer.participants or outer.preconditions):
        message = "a fresh action cannot start after the admitting transaction staged changes"
        raise FreshActionError(message)
    token = _CURRENT.set(None)
    try:
        with transaction(action_context=action_context) as handle:
            yield handle
    finally:
        _CURRENT.reset(token)


@contextmanager
def batch() -> Iterator[None]:
    """Coalesce related state writes into one invalidation per component."""
    with transaction():
        yield


@contextmanager
def readonly_transaction(*, action_context: ActionContext | None = None) -> Iterator[Transaction]:
    """Run an identified read-only action and reject any state mutation."""
    if _CURRENT.get() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    causality = current_causality()
    context = (
        action_context
        or current_action()
        or ActionContext.create(
            "read-only action",
            cause=None if causality is None else causality[0],
            root_action_id=None if causality is None else causality[1],
        )
    )
    current = _Transaction(readonly=True, owner_task=_current_task(), context=context)
    with action_scope(context):
        token = _CURRENT.set(current)
        try:
            _checkpoint("transaction.enter_body")
            yield current.handle
        except BaseException as error:
            _CURRENT.reset(token)
            current.closed = True
            _emit_rollback(current, error, during_commit=False)
            raise
        _CURRENT.reset(token)
        try:
            _checkpoint("transaction.close_staging")
            with _COMMIT_GATE:
                commit = current.commit()
        except BaseException as error:
            current.closed = True
            _emit_rollback(current, error, during_commit=True)
            raise
        _checkpoint("commit.gate_released")
        current.finalize_commit()
        current.closed = True
        _emit_commit(current, commit)


def on_action_commit(
    callback: Callable[[ActionCommit, ActionContinuation], None], *, key: object | None = None
) -> None:
    """Register a synchronous callback for a successful commit.

    Call inside a transaction. Callback failures are reported without changing
    the result. Use the supplied continuation to start a new action for writes.
    """
    _add_action_hook("commit", callback, key=key)


def on_action_rollback(
    callback: Callable[[ActionRollback, ActionContinuation], None], *, key: object | None = None
) -> None:
    """Register a synchronous callback after rollback; its errors are reported."""
    _add_action_hook("rollback", callback, key=key)


def on_action_result(
    callback: Callable[[ActionResult, ActionContinuation], None], *, key: object | None = None
) -> None:
    """Register a synchronous callback for either commit or rollback."""
    _add_action_hook("result", callback, key=key)


def _add_action_hook(kind: str, callback: Callable[..., None], *, key: object | None) -> None:
    current = _CURRENT.get()
    if current is None:
        message = "commit hooks are only available inside an action's transaction"
        raise RuntimeError(message)
    if not current.writable():
        current.reject_stale("a commit hook")
    if key is not None and has_action_hook(key):
        message = f"{key!r} already registered a commit hook for this action"
        raise RuntimeError(message)
    hooks = {
        "commit": current.commit_hooks,
        "rollback": current.rollback_hooks,
        "result": current.result_hooks,
    }[kind]
    hooks.append((key, callback))


def enlist[ParticipantT: TransactionParticipant[Any]](
    key: object, factory: Callable[[], ParticipantT]
) -> ParticipantT | None:
    """Take part in the action in flight, staging writes instead of publishing them.

    Returns the participant registered for `key`, built by `factory` on first use, or
    `None` when no transaction is open -- the caller's signal that its write has nothing
    to wait for and should land now. `key` identifies the subsystem, is usually the
    subsystem itself, and must be hashable.
    """
    current = _CURRENT.get()
    if current is None:
        return None
    return current.enlist(key, factory)


def action_participant(key: object) -> TransactionParticipant[Any] | None:
    """Return the participant already registered for ``key``, without creating one."""
    current = _CURRENT.get()
    return None if current is None else current.participants.get(key)


def has_action_hook(key: object) -> bool:
    """Whether `key` already registered any result hook for the action in flight."""
    current = _CURRENT.get()
    if current is None:
        return False
    return any(
        registered is key
        for hooks in (current.commit_hooks, current.rollback_hooks, current.result_hooks)
        for registered, _ in hooks
    )


@contextmanager
def block_writes(reason: str) -> Iterator[None]:
    """Reject component-state writes for the duration of the block.

    Narrower than `readonly_transaction`: the transaction stays open and keeps everything it
    has recorded. For code that runs *inside* an action but must not touch the tree, such as
    an undo entry's external inverse, whose writes a following restore would silently clobber.
    """
    current = _CURRENT.get()
    if current is None:
        yield
        return
    previous = current.write_block
    current.write_block = reason
    try:
        yield
    finally:
        current.write_block = previous


class _State:
    """Descriptor backing a declared reactive state field."""

    def __init__(
        self,
        default: Any = _MISSING,
        *,
        factory: Callable[[], Any] | None = None,
        persist: bool | None = None,
        opaque: bool = False,
    ) -> None:
        if default is not _MISSING and factory is not None:
            message = "state accepts either a default or a factory, not both"
            raise TypeError(message)
        if opaque and persist:
            message = "opaque state cannot be persisted; it is not serializable by assumption"
            raise TypeError(message)
        self._default = default
        self._factory = factory
        self._name = ""
        self.public_name = ""
        self.opaque = opaque
        self.persist = (not opaque) if persist is None else persist
        self.persist_declared = persist is not None
        """Whether the author asked for persistence, as opposed to taking the default.

        An owner that cannot persist anything needs to tell the difference, so it can refuse
        `persist=True` without refusing every field that merely defaulted to it.
        """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__state_{name}"
        self.public_name = name

    @property
    def has_initial(self) -> bool:
        return self._default is not _MISSING or self._factory is not None

    def _initial(self, instance: ReactiveOwner) -> Any:
        if self._factory is None:
            # No copy: a state value is replaced, never mutated, so instances may share it.
            return self._default
        return self._factory()

    def cell(self, instance: ReactiveOwner) -> _Cell:
        """Return this field's storage on `instance`, empty until something assigns it."""
        cell = instance.__dict__.get(self._name)
        if cell is None:
            cell = instance.__dict__[self._name] = _Cell(address=CellAddress(instance, self.public_name))
        return cell

    def is_set(self, instance: ReactiveOwner) -> bool:
        """Whether this field currently holds a value of its own rather than its default."""
        cell = instance.__dict__.get(self._name)
        return cell is not None and (cell.value is not _MISSING or _staged_value(cell) is not _MISSING)

    def mutated(self, instance: ReactiveOwner) -> None:
        """Stage a version change for an opaque value that was mutated in place.

        The mutation itself cannot be undone. Staging delays its notification until
        commit, so a failed action does not publish a change notification. Bypass
        __set__ because its identity comparison would discard this same-object write.
        """
        cell = self.cell(instance)
        held = _staged_value(cell)
        if held is _MISSING:
            held = cell.value
        _write(instance, self._name, cell, held)

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cell = instance.__dict__.get(self._name)
        # `read()` answers with this action's staged value wherever it has one, so a slot the
        # action emptied reads as empty rather than handing back the sentinel that spells it.
        # Asking the cell first is what keeps that sentinel private: deciding on `cell.value`
        # and only then reading meant a staged absence was returned to the caller verbatim.
        held = _MISSING if cell is None else cell.read()
        if held is not _MISSING:
            return held
        if not self.has_initial:
            message = f"{type(instance).__name__}.{self.public_name} was never assigned"
            raise AttributeError(message)
        cell = self.cell(instance)
        if cell.value is _MISSING:
            # Materializing a default is not a write: no version bump, no invalidation.
            cell.value = self._initial(instance)
            return cell.read()
        # The action in flight emptied this slot. Empty reads as the declared default, and
        # that default is not installed over the committed value: outside the action the
        # committed value is still what everyone sees, and the commit is about to remove it
        # rather than replace it with this.
        return self._initial(instance)

    def __set__(self, instance: ReactiveOwner, value: Any) -> None:
        _check_write(instance, self.public_name)
        cell = instance.__dict__.get(self._name)
        if cell is not None:
            held = _staged_value(cell)
            if held is _MISSING:
                held = cell.value
            # Opaque fields compare by identity: their values are collaborators, and `==` on
            # one is the author's code, not a cheap settled-value check.
            if held is value if self.opaque else _equal(held, value):
                return
        _write(instance, self._name, self.cell(instance), value)

    def __delete__(self, instance: ReactiveOwner) -> None:
        """Reject deletion and direct callers to reset the field by assignment.

        History can still restore an unassigned slot through the internal write
        path, preserving its identity and version.
        """
        message = (
            f"{type(instance).__name__}.{self.public_name} cannot be deleted; a state field is "
            f"reset, not removed. Assign the value it should hold instead."
        )
        raise AttributeError(message)


@overload
def state[KeyT, ValueT](
    default: dict[KeyT, ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> Mapping[KeyT, ValueT]: ...


@overload
def state[ValueT](
    default: list[ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> Sequence[ValueT]: ...


@overload
def state[ValueT](
    default: set[ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> AbstractSet[ValueT]: ...


@overload
def state[ValueT](
    default: ValueT,
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> ValueT: ...


@overload
def state[KeyT, ValueT](
    *,
    factory: Callable[[], dict[KeyT, ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> Mapping[KeyT, ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], list[ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> Sequence[ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], set[ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> AbstractSet[ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], ValueT],
    persist: bool | None = None,
    opaque: bool = False,
) -> ValueT: ...


@overload
def state(*, persist: bool | None = None, opaque: bool = False) -> Any: ...


def state(
    default: Any = _MISSING,
    *,
    factory: Callable[[], Any] | None = None,
    persist: bool | None = None,
    opaque: bool = False,
) -> Any:
    """Declare a reactive field whose assignments participate in transactions.

    Supply either a default or a factory, or leave both out and assign the field
    in __init__. The factory runs when an unassigned field is first read.

    Replace ordinary values rather than mutate them in place. For example, use
    ``self.items = (*self.items, item)`` to notify readers of an added item.
    Collection overloads expose read-only interfaces to type checkers, but the
    actual stored objects are not wrapped or copied.

    Args:
        default: Initial value, shared by reference across instances.
        factory: Build an initial value for each instance on demand.
        persist: Include the field in exported state. Defaults to True unless opaque.
        opaque: Hold a collaborator such as a service or session. Compare changes
            by identity and exclude the field from persistence.
    """
    return _State(default, factory=factory, persist=persist, opaque=opaque)


@dataclass(frozen=True, slots=True)
class CellReport:
    """One state field as devtools sees it."""

    identity: int
    """`id()` of the cell, so a computed's sources can be resolved back to a name."""
    version: int
    assigned: bool
    """Whether the field holds a value of its own rather than its declared default."""
    opaque: bool


@dataclass(frozen=True, slots=True)
class ComputedReport:
    """One computed as devtools sees it, including what it currently depends on."""

    identity: int
    evaluated: bool
    """False until something reads it. A computed nobody renders is never evaluated."""
    version: int
    sources: tuple[int, ...]
    """Identities of the cells and computeds its last run read, in read order."""


def inspect_cells(owner: ReactiveOwner) -> dict[str, CellReport]:
    """Report every declared state field on `owner`, by public name."""
    reports: dict[str, CellReport] = {}
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                reports[name] = CellReport(id(cell), cell.settle(), descriptor.is_set(owner), descriptor.opaque)
    return reports


def inspect_computed(owner: ReactiveOwner) -> dict[str, ComputedReport]:
    """Report every computed on `owner`, without evaluating one that has never run."""
    reports: dict[str, ComputedReport] = {}
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _Computed):
                node = descriptor.node(owner)
                reports[name] = ComputedReport(
                    id(node), node.evaluated, node.version, tuple(id(source) for source in node.sources)
                )
    return reports


def declared_cells(owner: ReactiveOwner) -> dict[_Cell, int]:
    """Return all declared cells and their current versions.

    Resources use these as initial dependencies when replace supplies a value
    before the loader has run. The first load records the actual dependencies.
    """
    presumed: dict[_Cell, int] = {}
    for klass in type(owner).__mro__:
        for descriptor in vars(klass).values():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                presumed[cell] = cell.settle()
    return presumed


def _state_fields(owner: ReactiveOwner) -> dict[str, _State]:
    fields: dict[str, _State] = {}
    for cls in reversed(type(owner).__mro__):
        fields.update(
            (name, descriptor)
            for name, descriptor in vars(cls).items()
            if isinstance(descriptor, _State) and descriptor.persist
        )
    return fields


def export_state(owner: ReactiveOwner) -> dict[str, Any]:
    """Return the value of every persistent state descriptor that currently has one."""
    return {
        name: getattr(owner, name)
        for name, descriptor in _state_fields(owner).items()
        if descriptor.has_initial or descriptor.is_set(owner)
    }


def restore_state(owner: ReactiveOwner, values: Mapping[str, Any]) -> None:
    """Restore declared persistent state, rejecting stale or misspelled field names."""
    fields = _state_fields(owner)
    unknown = set(values) - set(fields)
    if unknown:
        message = f"snapshot has unknown state fields: {', '.join(sorted(unknown))}"
        raise ValueError(message)
    with transaction():
        for name, value in values.items():
            setattr(owner, name, _frozen(value))


class _Derived:
    """One computed's per-instance node: what it read last, and what it returned.

    Sources are held by the node, never the other way round. A component here is per-message
    and constantly dropped, and a source holding its readers would keep every one of them
    alive; a version comparison at read time answers the same question with no back-edge.
    """

    __slots__ = ("_epoch", "_function", "_label", "_settled", "owner", "sources", "value", "version")

    def __init__(self, function: Callable[[Any], Any], owner: ReactiveOwner, label: str) -> None:
        self._function = function
        self._label = label
        self.owner = owner
        self.sources: dict[_ReactiveSource, int] = {}
        self.value: Any = None
        self.version = 0
        self._settled = False
        self._epoch = -1

    @property
    def address(self) -> None:
        return None

    def dependencies(self) -> Iterable[_ReactiveSource]:
        return self.sources

    def settle(self) -> int:
        """Return this node's current version, recomputing only if a source moved."""
        # An active transaction may expose staged versions instead of committed versions.
        # Check those dependencies even when this node has already seen the current epoch.
        current = _active()
        if self._epoch == _EPOCH and (current is None or not current.writes):
            return self.version
        if self._settled and all(source.settle() == seen for source, seen in self.sources.items()):
            self._epoch = _EPOCH
            return self.version
        # Cleared before the run, not after: a body that raises must leave the node asking to
        # be recomputed rather than holding a value nothing verified.
        self._settled = False
        self._epoch = -1
        self.sources = {}
        token = _CONSUMER.set(self)
        try:
            with settling(self), read_only():
                value = self._function(self.owner)
        finally:
            _CONSUMER.reset(token)
        self._settled = True
        # Recomputing is not a write, so the epoch stays put: nothing downstream can be stale
        # from it, and bumping would make every settled node in the process walk its sources.
        self._epoch = _EPOCH
        if not _equal(self.value, value):
            self.value = value
            self.version += 1
        return self.version

    @property
    def evaluated(self) -> bool:
        """Whether this node has ever produced a value. Reading it is what does that."""
        return self._settled

    def read(self) -> Any:
        self.settle()
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version
        return self.value


class _Computed:
    def __init__(self, function: Callable[[Any], Any]) -> None:
        self._function = function
        self.public_name = function.__name__
        self._name = f"__computed_{function.__name__}"
        self._label = function.__name__

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__computed_{name}"
        self._label = f"{owner.__name__}.{name}"

    def node(self, instance: ReactiveOwner) -> _Derived:
        """Return this computed's node on `instance`, unevaluated until something reads it."""
        node = instance.__dict__.get(self._name)
        if node is None:
            node = instance.__dict__[self._name] = _Derived(self._function, instance, self._label)
        return node

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return self.node(instance).read()


def computed[ValueT](function: Callable[[Any], ValueT]) -> ValueT:
    """Cache a derived value and track the reactive fields its function reads.

    The function runs on first access and again when a dependency version
    changes. Each run replaces the dependency set, so conditional branches can
    change what is tracked. If recomputation produces an equal value, dependents
    keep seeing the same computed version. The function runs read-only.
    """
    # The declared return type describes what reading the attribute yields, not what the
    # decorator hands back: the descriptor below is what actually gets bound to the class.
    return _Computed(function)  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-return]


@dataclass(slots=True)
class Dependencies:
    """Record dependencies read by one render or computation.

    Addresses are collected through computed values and resources as well as
    direct cell reads. Shared state is tracked automatically; watch adds a
    dependency on a topic that has no value to read.
    """

    sources: dict[_ReactiveSource, int] = field(default_factory=dict)

    def current(self) -> bool:
        """Whether every directly observed reactive source still yields the seen version.

        Settling a computed here preserves its backdating contract: an upstream write that
        recomputes to the same value leaves the computed's version unchanged and therefore does
        not invalidate a cached pure projection.
        """
        return all(source.settle() == seen for source, seen in tuple(self.sources.items()))

    def addresses(self) -> tuple[Any, ...]:
        """Every addressed cell this run reached, deduplicated, in read order.

        Anything with sources of its own is walked rather than re-run -- a cached computed, a
        settled resource. It did not read its sources again, but a reader that used its value
        still depends on every one of them, so a topic watched two loaders down is still this
        render's dependency. One that carries an address of its own contributes that too.
        """
        found: list[Any] = []
        seen: set[int] = set()

        def walk(sources: Iterable[_ReactiveSource]) -> None:
            for source in sources:
                identity = id(source)
                if identity in seen:
                    continue
                seen.add(identity)
                address = source.address
                if address is not None:
                    found.append(address)
                walk(source.dependencies())

        walk(self.sources)
        return tuple(found)


@contextmanager
def capture_reads() -> Iterator[Dependencies]:
    """Collect direct reactive dependencies without changing write permissions.

    Nested captures record their own reads. Computed values and resources remain
    direct dependencies; their upstream notification addresses are resolved on demand.
    """
    dependencies = Dependencies()
    token = _CONSUMER.set(dependencies)
    try:
        yield dependencies
    finally:
        _CONSUMER.reset(token)


@contextmanager
def observe_reads() -> Iterator[Dependencies]:
    """Collect dependencies of a read-only computation."""
    with read_only(), capture_reads() as dependencies:
        yield dependencies


def addresses(read: Callable[[], object]) -> tuple[Any, ...]:
    """Return the addresses read by a callable, raising if none are found.

    For example, ``addresses(lambda: preferences.theme)`` returns the address
    of that field. Reads through computed values include their source
    addresses. The callable runs as a read-only computation.
    """
    with observe_reads() as observation:
        read()
    found = observation.addresses()
    if not found:
        message = (
            "addresses() read no reactive state: the callable must read at least one state() "
            "field on a Reactive object, directly or through a computed."
        )
        raise ValueError(message)
    return found


def _checked_init(
    original: Callable[..., None],
    required: tuple[tuple[str, _State], ...],
) -> Callable[..., None]:
    """Wrap an initializer so required reactive state is assigned before it returns."""

    @functools.wraps(original)
    def __init__(self: Reactive, *args: Any, **kwargs: Any) -> None:
        try:
            original(self, *args, **kwargs)
            if type(self).__init__ is not __init__:
                return
            missing = sorted(name for name, descriptor in required if not descriptor.is_set(self))
            if missing:
                message = f"{type(self).__name__}.__init__ left declared state unassigned: {', '.join(missing)}"
                raise TypeError(message)
        finally:
            if type(self).__init__ is __init__:
                vars(self).pop("_reactive_initialization", None)

    return __init__


class Reactive:
    """Reusable reactive state and behavior, independent of rendering.

    Declare fields with `state()` and derived properties with `computed`. Consumers
    automatically follow fields they read through a subscription reconciler. Fields without
    a default must be assigned before initialization finishes.

    Construction may assign ordinary attributes. Once the owner exists, writes
    inside a transaction must target declared state so failure can discard them.
    Opaque collaborator changes are signaled through `mutated`; that delays their
    notifications but cannot undo mutations already made to the collaborator.
    """

    _reactive_commit_listeners: set[Callable[[], None]]

    _state_names: ClassVar[frozenset[str]] = frozenset()
    _state_descriptors: ClassVar[dict[str, _State]] = {}
    _opaque_state: ClassVar[tuple[tuple[str, _State], ...]] = ()
    _computed_descriptors: ClassVar[dict[str, _Computed]] = {}
    _reactive_internal_attributes: ClassVar[frozenset[str]] = frozenset()
    _reactive_require_state: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = {
            name: descriptor
            for klass in reversed(cls.__mro__)
            for name, descriptor in vars(klass).items()
            if isinstance(descriptor, _State)
        }
        cls._state_names = frozenset(declared)
        cls._state_descriptors = declared
        cls._opaque_state = tuple((name, descriptor) for name, descriptor in declared.items() if descriptor.opaque)
        cls._computed_descriptors = {
            name: descriptor
            for klass in reversed(cls.__mro__)
            for name, descriptor in vars(klass).items()
            if isinstance(descriptor, _Computed)
        }
        required = tuple((name, descriptor) for name, descriptor in declared.items() if not descriptor.has_initial)
        cls.__init__ = _checked_init(cls.__init__, required if cls._reactive_require_state else ())

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        instance = super().__new__(cls)
        # Subclasses can initialize their own inputs without calling a base initializer.
        object.__setattr__(instance, "_reactive_commit_listeners", set())
        object.__setattr__(instance, "_reactive_initialization", _READ_ONLY.get())
        if current := _CURRENT.get():
            current.note_born(instance)
        return instance

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if (
            _active() is not None
            and name not in type(self)._reactive_internal_attributes
            and name not in type(self)._state_names
        ):
            report_undeclared_write(self, name)
        object.__setattr__(self, name, value)

    def _state_rolled_back(self) -> None:
        self.on_state_rollback()

    def on_state_commit(self, names: frozenset[str]) -> None:
        """React after the named state fields publish together."""

    def on_state_rollback(self) -> None:
        """React after an attempted transaction restores this owner."""

    def mutated(self, collaborator: object) -> None:
        """Signal an in-place change to the object held by one opaque state field.

        Inside an action the signal stages with it, so a handler that fails publishes no
        notification and a namespace does not tell other mounts to re-read halfway through
        a transaction that is about to roll back.
        """
        with untracked():
            holders = [
                (name, descriptor)
                for name, descriptor in type(self)._opaque_state
                if descriptor.is_set(self) and descriptor.__get__(self) is collaborator
            ]
        if not holders:
            message = f"no opaque state on {type(self).__name__} holds {collaborator!r}"
            raise TypeError(message)
        if len(holders) > 1:
            names = ", ".join(name for name, _ in holders)
            message = f"{type(self).__name__} holds {collaborator!r} in more than one field ({names})"
            raise TypeError(message)
        _, descriptor = holders[0]
        # No notification of its own: the staged write carries one, delivered at the commit
        # outside an action and at `finalize_commit` inside one.
        descriptor.mutated(self)

    def _state_changed(self, names: frozenset[str]) -> None:
        for descriptor in type(self)._state_descriptors.values():
            if descriptor._name in names:
                publish(CellAddress(self, descriptor.public_name))
        for listener in tuple(self._reactive_commit_listeners):
            try:
                listener()
            except Exception:
                _log.exception("reactive commit listener failed")
        self.on_state_commit(names)

    def _add_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.add(listener)

    def _remove_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.discard(listener)
