"""Identity addresses and weak subscriptions for live reactive objects.

Consumers retain subscription handles; sources keep weak references to those
handles so an abandoned consumer is never kept alive by its model.
"""

import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, eq=False)
class CellAddress:
    """Identify a reactive field by owner identity and field name."""

    owner: object
    name: str

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CellAddress):
            return NotImplemented
        return other.owner is self.owner and other.name == self.name

    @override
    def __hash__(self) -> int:
        return hash((id(self.owner), self.name))

    @override
    def __str__(self) -> str:
        """Return a diagnostic name such as ``Preferences(7).theme``."""
        return f"{self.owner!r}.{self.name}"


@dataclass(eq=False, slots=True, weakref_slot=True)
class _Subscription:
    callback: Callable[[CellAddress], None]
    active: bool = True


def subscribe(owner: object, name: str, callback: Callable[[CellAddress], None]) -> Callable[[], None]:
    """Subscribe until the returned callable is called or released."""
    subscribers = vars(owner).setdefault("_reactive_subscribers", {})
    entries = subscribers.setdefault(name, [])
    subscription = _Subscription(callback)
    reference = weakref.ref(subscription)
    entries.append(reference)

    def unsubscribe() -> None:
        if subscription.active:
            subscription.active = False
            entries.remove(reference)
            if not entries:
                subscribers.pop(name, None)

    return unsubscribe


def publish(address: CellAddress) -> None:
    """Notify current consumers, isolating failures and pruning expired handles."""
    subscribers = vars(address.owner).get("_reactive_subscribers", {})
    entries = subscribers.get(address.name, [])
    for reference in tuple(entries):
        subscription = reference()
        if subscription is None:
            if reference in entries:
                entries.remove(reference)
        elif subscription.active:
            try:
                subscription.callback(address)
            except Exception:
                _logger.exception("reactive subscriber failed for %s", address)
    if not entries:
        subscribers.pop(address.name, None)
