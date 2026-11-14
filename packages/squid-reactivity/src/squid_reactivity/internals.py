"""Private integration points for other Squid UI packages.

The component runtime and replication bindings need a few pieces of reactive
storage and resource loading that applications never touch. These exports may
change in any release. Application code imports from ``squid_reactivity``.
"""

from squid_reactivity.async_values import _Execution as Execution
from squid_reactivity.cells import _Cell as Cell
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending

__all__ = [
    "AtomicResourcePending",
    "Cell",
    "Execution",
]
