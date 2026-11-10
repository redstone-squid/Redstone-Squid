"""Private integration points for other Squid UI packages.

The component runtime and replication bindings use these exports to work with
reactive storage and transactions. They may change in any release. Application
code should import from ``squid_reactivity`` instead.
"""

from squid_reactivity.core import (
    _State as StateDescriptor,
)
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending
from squid_reactivity.tracking import (
    _CURRENT as CURRENT_TRANSACTION,
)
from squid_reactivity.tracking import (
    _Cell as Cell,
)
from squid_reactivity.tracking import (
    _Signal as Signal,
)
from squid_reactivity.transactions import (
    _Transaction as Transaction,
)

__all__ = [
    "CURRENT_TRANSACTION",
    "AtomicResourcePending",
    "Cell",
    "Signal",
    "StateDescriptor",
    "Transaction",
]
