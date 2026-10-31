"""Optional real-backend adapters; importing this package installs no backend."""

from squid_replicated.backends.loro import LoroTextEngine
from squid_replicated.backends.pycrdt import PycrdtTextEngine

__all__ = ["LoroTextEngine", "PycrdtTextEngine"]
