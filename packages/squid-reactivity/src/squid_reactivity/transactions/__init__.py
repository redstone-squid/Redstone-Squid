"""Transactions, their identities, outcomes, and reversible changes.

Application code can import these names here or from squid_reactivity. Low-level
reactive modules import context, results, and state before cells are initialized,
so public API exports load on first access rather than importing the engine eagerly.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from squid_reactivity.transactions.context import ActorRef as ActorRef
    from squid_reactivity.transactions.context import Causality as Causality
    from squid_reactivity.transactions.context import CausalRef as CausalRef
    from squid_reactivity.transactions.context import ExceptionReport as ExceptionReport
    from squid_reactivity.transactions.context import TransactionContext as TransactionContext
    from squid_reactivity.transactions.context import TransactionId as TransactionId
    from squid_reactivity.transactions.context import TransactionPurpose as TransactionPurpose
    from squid_reactivity.transactions.context import TransactionRelation as TransactionRelation
    from squid_reactivity.transactions.engine import TransactionParticipant as TransactionParticipant
    from squid_reactivity.transactions.engine import TransactionView as TransactionView
    from squid_reactivity.transactions.engine import report_undeclared_write as report_undeclared_write
    from squid_reactivity.transactions.patches import CellPatch as CellPatch
    from squid_reactivity.transactions.patches import CellPatchSet as CellPatchSet
    from squid_reactivity.transactions.patches import CellTarget as CellTarget
    from squid_reactivity.transactions.patches import ConditionalCellPatch as ConditionalCellPatch
    from squid_reactivity.transactions.patches import SlotValue as SlotValue
    from squid_reactivity.transactions.results import ChangeReport as ChangeReport
    from squid_reactivity.transactions.results import ChangeToken as ChangeToken
    from squid_reactivity.transactions.results import CommitSequence as CommitSequence
    from squid_reactivity.transactions.results import ConflictDetail as ConflictDetail
    from squid_reactivity.transactions.results import RollbackReason as RollbackReason
    from squid_reactivity.transactions.results import TransactionCommit as TransactionCommit
    from squid_reactivity.transactions.results import TransactionContribution as TransactionContribution
    from squid_reactivity.transactions.results import TransactionResult as TransactionResult
    from squid_reactivity.transactions.results import TransactionRollback as TransactionRollback
    from squid_reactivity.transactions.scope import Transaction as Transaction
    from squid_reactivity.transactions.scope import apply_conditional_patches as apply_conditional_patches
    from squid_reactivity.transactions.scope import batch as batch
    from squid_reactivity.transactions.scope import current_transaction as current_transaction
    from squid_reactivity.transactions.scope import enlist as enlist
    from squid_reactivity.transactions.scope import fresh_transaction as fresh_transaction
    from squid_reactivity.transactions.scope import readonly_transaction as readonly_transaction
    from squid_reactivity.transactions.scope import transaction as transaction
    from squid_reactivity.transactions.scope import transaction_participant as transaction_participant
    from squid_reactivity.transactions.state import block_writes as block_writes
    from squid_reactivity.transactions.state import read_only as read_only
    from squid_reactivity.transactions.state import relaxed_read as relaxed_read
    from squid_reactivity.transactions.state import strong_read as strong_read

_EXPORT_MODULES = {
    "Transaction": "scope",
    "transaction": "scope",
    "readonly_transaction": "scope",
    "fresh_transaction": "scope",
    "batch": "scope",
    "current_transaction": "scope",
    "enlist": "scope",
    "transaction_participant": "scope",
    "apply_conditional_patches": "scope",
    "TransactionParticipant": "engine",
    "TransactionView": "engine",
    "report_undeclared_write": "engine",
    "SlotValue": "patches",
    "CellTarget": "patches",
    "CellPatch": "patches",
    "ConditionalCellPatch": "patches",
    "CellPatchSet": "patches",
    "TransactionContext": "context",
    "TransactionId": "context",
    "TransactionPurpose": "context",
    "TransactionRelation": "context",
    "ActorRef": "context",
    "CausalRef": "context",
    "Causality": "context",
    "ExceptionReport": "context",
    "ChangeReport": "results",
    "ChangeToken": "results",
    "CommitSequence": "results",
    "ConflictDetail": "results",
    "RollbackReason": "results",
    "TransactionCommit": "results",
    "TransactionContribution": "results",
    "TransactionResult": "results",
    "TransactionRollback": "results",
    "block_writes": "state",
    "read_only": "state",
    "relaxed_read": "state",
    "strong_read": "state",
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
    "batch",
    "block_writes",
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
