"""Compatibility exports for shared state from :mod:`squid_reactivity`."""

from squid_reactivity.state_pool import SharedStateFactory, SharedStatePool
from squid_reactivity.shared_state import SharedState, describe

__all__ = ["SharedState", "SharedStateFactory", "SharedStatePool", "describe"]
