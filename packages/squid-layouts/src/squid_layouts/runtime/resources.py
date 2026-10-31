"""Compatibility exports for reactive resources.

New code that does not need the layout frontend can import this optional layer from
``squid_reactive.resources`` directly.
"""

from squid_reactive.resources import (
    AsyncBinding,
    AtomicResource,
    AtomicResourceStatus,
    Failed,
    Pending,
    PendingPolicy,
    Ready,
    Resource,
    ResourceNotReadyError,
    ResourceStatus,
    _AtomicResourcePending,
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
    "Pending",
    "PendingPolicy",
    "Ready",
    "Resource",
    "ResourceNotReadyError",
    "ResourceStatus",
    "_AtomicResourcePending",
    "observe_async_bindings",
    "observe_resources",
    "resource",
    "unique_async_bindings",
    "unique_resources",
]
