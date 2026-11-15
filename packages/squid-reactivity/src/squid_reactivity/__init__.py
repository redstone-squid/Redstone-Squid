"""Track dependencies between model fields and publish related changes together.

Declare fields with state() and derived values with computed() on a Reactive model.
Reads record dependencies; assignments advance versions and notify subscribers.
A transaction stages related writes and publishes them when its block succeeds.
Resources cache asynchronous loads; operations expose explicitly started work
and its progress.

Models and their consumers belong to one thread. Independent threads may use
separate reactive objects when started with separate execution contexts.
"""

from squid_reactivity._cells import Signal
from squid_reactivity.core import Reactive, computed, export_state, restore_state, state
from squid_reactivity.diagnostics import EventLedger, observe_events
from squid_reactivity.errors import (
    FrameworkIntegrityError,
    FreshTransactionError,
    ReactiveConflictError,
    ReactiveCycleError,
    ReactiveWriteError,
    ReactivityError,
    StaleReactiveContextError,
    TransactionValidationError,
    UndeclaredStateError,
)
from squid_reactivity.subscriptions import Subscriptions
from squid_reactivity.topics import CellAddress, DependencyAddress, LocalTopicBus, Topic, TopicBus, TopicName, watch
from squid_reactivity.tracking import Dependencies, capture_reads, observe_reads, untracked
from squid_reactivity.transactions import Transaction, current_transaction, readonly_transaction, transaction
from squid_reactivity.transactions._results import TransactionCommit, TransactionResult, TransactionRollback
from squid_reactivity.transactions._state import relaxed_read, strong_read

__all__ = [
    "CellAddress",
    "Dependencies",
    "DependencyAddress",
    "EventLedger",
    "FrameworkIntegrityError",
    "FreshTransactionError",
    "LocalTopicBus",
    "Reactive",
    "ReactiveConflictError",
    "ReactiveCycleError",
    "ReactiveWriteError",
    "ReactivityError",
    "Signal",
    "StaleReactiveContextError",
    "Subscriptions",
    "Topic",
    "TopicBus",
    "TopicName",
    "Transaction",
    "TransactionCommit",
    "TransactionResult",
    "TransactionRollback",
    "TransactionValidationError",
    "UndeclaredStateError",
    "capture_reads",
    "computed",
    "current_transaction",
    "export_state",
    "observe_events",
    "observe_reads",
    "readonly_transaction",
    "relaxed_read",
    "restore_state",
    "state",
    "strong_read",
    "transaction",
    "untracked",
    "watch",
]
