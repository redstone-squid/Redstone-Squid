"""Compatibility exports for shared state from :mod:`squid_reactivity`."""

from squid_reactivity.pool import SharedFactory, SharedPool
from squid_reactivity.shared import Shared, describe

__all__ = ["Shared", "SharedFactory", "SharedPool", "describe"]
