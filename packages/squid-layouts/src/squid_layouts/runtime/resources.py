"""Compatibility exports for reactive resources.

New code that does not need the layout frontend can import this optional layer from
``squid_reactive.resources`` directly.
"""

from squid_reactive.resources import (
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
    observe_resources,
    resource,
    unique_resources,
)

__all__ = [
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
    "observe_resources",
    "resource",
    "unique_resources",
]
