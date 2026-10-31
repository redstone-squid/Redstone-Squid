"""Reactive-core internals exported for the rest of the Squid UI suite.

Nothing here is supported public API and nothing here is covered by the alpha stability
promise: these names exist so sibling distributions -- squid-ui's component runtime and
squid-replication's document binding -- can integrate with the reactive core without
importing private names across a distribution boundary, and they may change in any
release. Application code should import from :mod:`squid_reactivity` instead.
"""

from squid_reactivity.core import _CURRENT as CURRENT_TRANSACTION
from squid_reactivity.core import _RENDER_OBSERVATION as RENDER_OBSERVATION
from squid_reactivity.core import _Cell as Cell
from squid_reactivity.core import _State as StateDescriptor
from squid_reactivity.core import _Transaction as Transaction
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending

__all__ = [
    "CURRENT_TRANSACTION",
    "RENDER_OBSERVATION",
    "AtomicResourcePending",
    "Cell",
    "StateDescriptor",
    "Transaction",
]
