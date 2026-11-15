"""Transactions, their identities, outcomes, and reversible changes.

Application code can import these names here or from squid_reactivity. Low-level
reactive modules import _context, _results, and _state before cells are initialized,
so public API exports load on first access rather than importing the engine eagerly.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from squid_reactivity.transactions._context import ActorRef as ActorRef
    from squid_reactivity.transactions._context import Causality as Causality
    from squid_reactivity.transactions._context import CausalRef as CausalRef
    from squid_reactivity.transactions._context import ExceptionReport as ExceptionReport
    from squid_reactivity.transactions._context import TransactionContext as TransactionContext
    from squid_reactivity.transactions._context import TransactionId as TransactionId
    from squid_reactivity.transactions._context import TransactionPurpose as TransactionPurpose
    from squid_reactivity.transactions._context import TransactionRelation as TransactionRelation
    from squid_reactivity.transactions._context import current_causality as current_causality
    from squid_reactivity.transactions._engine import TransactionParticipant as TransactionParticipant
    from squid_reactivity.transactions._engine import TransactionView as TransactionView
    from squid_reactivity.transactions._engine import report_undeclared_write as report_undeclared_write
    from squid_reactivity.transactions._patches import CellPatch as CellPatch
    from squid_reactivity.transactions._patches import CellPatchSet as CellPatchSet
    from squid_reactivity.transactions._patches import CellTarget as CellTarget
    from squid_reactivity.transactions._patches import ConditionalCellPatch as ConditionalCellPatch
    from squid_reactivity.transactions._patches import SlotValue as SlotValue
    from squid_reactivity.transactions._results import ChangeReport as ChangeReport
    from squid_reactivity.transactions._results import ChangeToken as ChangeToken
    from squid_reactivity.transactions._results import CommitSequence as CommitSequence
    from squid_reactivity.transactions._results import ConflictDetail as ConflictDetail
    from squid_reactivity.transactions._results import RollbackReason as RollbackReason
    from squid_reactivity.transactions._results import TransactionCommit as TransactionCommit
    from squid_reactivity.transactions._results import TransactionContribution as TransactionContribution
    from squid_reactivity.transactions._results import TransactionResult as TransactionResult
    from squid_reactivity.transactions._results import TransactionRollback as TransactionRollback
    from squid_reactivity.transactions._scope import Transaction as Transaction
    from squid_reactivity.transactions._scope import apply_conditional_patches as apply_conditional_patches
    from squid_reactivity.transactions._scope import current_transaction as current_transaction
    from squid_reactivity.transactions._scope import enlist as enlist
    from squid_reactivity.transactions._scope import fresh_transaction as fresh_transaction
    from squid_reactivity.transactions._scope import readonly_transaction as readonly_transaction
    from squid_reactivity.transactions._scope import transaction as transaction
    from squid_reactivity.transactions._scope import transaction_participant as transaction_participant
    from squid_reactivity.transactions._state import block_writes as block_writes
    from squid_reactivity.transactions._state import read_only as read_only
    from squid_reactivity.transactions._state import relaxed_read as relaxed_read
    from squid_reactivity.transactions._state import strong_read as strong_read

_EXPORT_MODULES = {
    "Transaction": "_scope",
    "transaction": "_scope",
    "readonly_transaction": "_scope",
    "fresh_transaction": "_scope",
    "current_transaction": "_scope",
    "enlist": "_scope",
    "transaction_participant": "_scope",
    "apply_conditional_patches": "_scope",
    "TransactionParticipant": "_engine",
    "TransactionView": "_engine",
    "report_undeclared_write": "_engine",
    "SlotValue": "_patches",
    "CellTarget": "_patches",
    "CellPatch": "_patches",
    "ConditionalCellPatch": "_patches",
    "CellPatchSet": "_patches",
    "TransactionContext": "_context",
    "TransactionId": "_context",
    "TransactionPurpose": "_context",
    "TransactionRelation": "_context",
    "ActorRef": "_context",
    "CausalRef": "_context",
    "Causality": "_context",
    "ExceptionReport": "_context",
    "current_causality": "_context",
    "ChangeReport": "_results",
    "ChangeToken": "_results",
    "CommitSequence": "_results",
    "ConflictDetail": "_results",
    "RollbackReason": "_results",
    "TransactionCommit": "_results",
    "TransactionContribution": "_results",
    "TransactionResult": "_results",
    "TransactionRollback": "_results",
    "block_writes": "_state",
    "read_only": "_state",
    "relaxed_read": "_state",
    "strong_read": "_state",
}


def __getattr__(name: str) -> Any:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(message)
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _EXPORT_MODULES.keys())


__all__ = [
    "ActorRef",
    "CausalRef",
    "Causality",
    "CellPatch",
    "CellPatchSet",
    "CellTarget",
    "ChangeReport",
    "ChangeToken",
    "CommitSequence",
    "ConditionalCellPatch",
    "ConflictDetail",
    "ExceptionReport",
    "RollbackReason",
    "SlotValue",
    "Transaction",
    "TransactionCommit",
    "TransactionContext",
    "TransactionContribution",
    "TransactionId",
    "TransactionParticipant",
    "TransactionPurpose",
    "TransactionRelation",
    "TransactionResult",
    "TransactionRollback",
    "TransactionView",
    "apply_conditional_patches",
    "block_writes",
    "current_causality",
    "current_transaction",
    "enlist",
    "fresh_transaction",
    "read_only",
    "readonly_transaction",
    "relaxed_read",
    "report_undeclared_write",
    "strong_read",
    "transaction",
    "transaction_participant",
]
