"""Compatibility exports for shared state from :mod:`squid_reactive`."""

from squid_reactive.pool import SharedFactory, SharedPool
from squid_reactive.shared import Shared, describe

__all__ = ["Shared", "SharedFactory", "SharedPool", "describe"]
