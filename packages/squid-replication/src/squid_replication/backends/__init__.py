"""Optional real-backend adapters; `loro` and `pycrdt` are imported only when an engine is constructed."""

from squid_replication.backends.loro import LoroBackend, LoroTextEngine
from squid_replication.backends.pycrdt import PycrdtTextEngine

__all__ = ["LoroBackend", "LoroTextEngine", "PycrdtTextEngine"]
