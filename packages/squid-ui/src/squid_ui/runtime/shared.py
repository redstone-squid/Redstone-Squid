"""Re-exports `squid_reactivity`'s shared state under `squid_ui.runtime`; nothing here is defined by `squid_ui`."""

from squid_reactivity.shared_state import SharedState, describe
from squid_reactivity.state_pool import SharedStateFactory, SharedStatePool

__all__ = ["SharedState", "SharedStateFactory", "SharedStatePool", "describe"]
