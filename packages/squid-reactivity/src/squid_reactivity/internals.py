"""Private integration points for packages that build on squid-reactivity.

Storage and asynchronous execution implementations are exported here for closely
coupled integrations. These names may change in any release; applications should
use the public squid_reactivity API.

Squid UI and replication packages use these hooks to connect their own lifecycles
to reactive storage and resource loading.
"""

from squid_reactivity.async_values import _Execution as Execution
from squid_reactivity.cells import _Cell as Cell
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending

__all__ = [
    "AtomicResourcePending",
    "Cell",
    "Execution",
]
