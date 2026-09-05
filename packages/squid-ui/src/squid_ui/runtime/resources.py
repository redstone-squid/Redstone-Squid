"""Re-exports `squid_reactivity.resources` under `squid_ui.runtime`, plus `_AtomicResourcePending`.

`_AtomicResourcePending` is the `squid_reactivity.internals` exception a tree render catches to
abort on a pending atomic resource; nothing else here is defined by `squid_ui`.
"""

from squid_reactivity.internals import AtomicResourcePending as _AtomicResourcePending
from squid_reactivity.resources import (
    AsyncBinding,
    AtomicResource,
    AtomicResourceStatus,
    Failed,
    LoadScope,
    Pending,
    PendingMode,
    Ready,
    Resource,
    ResourceNotReadyError,
    ResourceStatus,
    abandon_superseded_loads,
    observe_async_bindings,
    observe_resources,
    resource,
    unique_async_bindings,
    unique_resources,
)

__all__ = [
    "AsyncBinding",
    "AtomicResource",
    "AtomicResourceStatus",
    "Failed",
    "LoadScope",
    "Pending",
    "PendingMode",
    "Ready",
    "Resource",
    "ResourceNotReadyError",
    "ResourceStatus",
    "_AtomicResourcePending",
    "abandon_superseded_loads",
    "observe_async_bindings",
    "observe_resources",
    "resource",
    "unique_async_bindings",
    "unique_resources",
]
