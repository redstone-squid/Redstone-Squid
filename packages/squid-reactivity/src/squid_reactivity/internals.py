"""Private integration points for packages that build on squid-reactivity.

Storage, notification, and asynchronous execution implementations are exported here
for closely coupled integrations. These names may change in any release; applications
should use the public squid_reactivity API.

For example, squid-ui and squid-replication use them to connect their own lifecycles
to reactive storage, subscriptions, and resource loading.
"""

from squid_reactivity._addresses import SubscriberRegistry, publish
from squid_reactivity._async_values import _Execution as Execution
from squid_reactivity._cells import _Cell as Cell
from squid_reactivity.resources import _AtomicResourcePending as AtomicResourcePending

__all__ = [
    "AtomicResourcePending",
    "Cell",
    "Execution",
    "SubscriberRegistry",
    "publish",
]
