"""Choose when the UI settles asynchronous values and presents their progress.

Operations represent explicitly requested work, so they keep running even when
there is no new scene to deliver and can reconcile progress while pending.
Resource loads support a projection and wait for its delivery. Their read mode
also determines whether the UI can present pending status or must first settle.
"""

from dataclasses import dataclass

from squid_reactivity.operations import OperationExecution
from squid_reactivity.resources import AsyncBinding, PendingMode, Resource


@dataclass(frozen=True, slots=True)
class AsyncRenderPolicy:
    """How a frontend schedules an observed asynchronous value."""

    pending_mode: PendingMode = PendingMode.EXPLICIT
    reconcile_while_pending: bool = False
    settle_without_delivery: bool = False


_RESOURCE = AsyncRenderPolicy()
_ATOMIC_RESOURCE = AsyncRenderPolicy(pending_mode=PendingMode.ATOMIC)
_OPERATION = AsyncRenderPolicy(reconcile_while_pending=True, settle_without_delivery=True)


def async_render_policy(binding: AsyncBinding) -> AsyncRenderPolicy:
    """Select scheduling policy without putting presentation flags on reactive values.

    Other AsyncBinding implementations use explicit pending status and settle
    after delivery, like ordinary resources.
    """
    if isinstance(binding, OperationExecution):
        return _OPERATION
    if isinstance(binding, Resource) and binding.pending_mode is PendingMode.ATOMIC:
        return _ATOMIC_RESOURCE
    return _RESOURCE
