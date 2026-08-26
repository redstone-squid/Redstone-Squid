"""Optional real-backend adapters; importing this package installs no backend."""

from squid_replication.backends.loro import LoroTextEngine
from squid_replication.backends.pycrdt import PycrdtTextEngine

__all__ = ["LoroTextEngine", "PycrdtTextEngine"]
