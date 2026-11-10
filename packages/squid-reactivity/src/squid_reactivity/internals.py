"""Private integration points for other Squid UI packages.

The component runtime and replication bindings use these exports to work with
reactive storage and transactions. They may change in any release. Application
code should import from ``squid_reactivity`` instead.
"""

from squid_reactivity.core import _CURRENT as CURRENT_TRANSACTION
from squid_reactivity.core import _RENDER_OBSERVATION as RENDER_OBSERVATION
from squid_reactivity.core import _Cell as Cell
from squid_reactivity.core import _Signal as Signal
from squid_reactivity.core import _State as StateDescriptor
from squid_reactivity.core import _Transaction as Transaction
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending

__all__ = [
    "CURRENT_TRANSACTION",
    "RENDER_OBSERVATION",
    "AtomicResourcePending",
    "Cell",
    "Signal",
    "StateDescriptor",
    "Transaction",
]
