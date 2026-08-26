"""Compatibility exports for reactive resources.

New code that does not need the layout frontend can import this optional layer from
``squid_reactivity.resources`` directly.
"""

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
    _AtomicResourcePending,
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
