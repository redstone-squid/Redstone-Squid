"""Compatibility exports for reactive resources.

New code that does not need the layout frontend can import this optional layer from
``squid_reactive.resources`` directly.
"""

from squid_reactive.resources import (
    AtomicResource,
    AtomicResourceState,
    Failed,
    Pending,
    Ready,
    Resource,
    ResourceDelivery,
    ResourceNotReadyError,
    ResourceState,
    _AtomicResourcePending,
    observe_resources,
    resource,
    unique_resources,
)

__all__ = [
    "AtomicResource",
    "AtomicResourceState",
    "Failed",
    "Pending",
    "Ready",
    "Resource",
    "ResourceDelivery",
    "ResourceNotReadyError",
    "ResourceState",
    "_AtomicResourcePending",
    "observe_resources",
    "resource",
    "unique_resources",
]
