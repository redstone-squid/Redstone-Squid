"""Detection of the optional native schematic engine.

The engine is an optional dependency with no musl or linux-aarch64 wheels, so every entry
point must tolerate its absence.
"""

import importlib.util
from functools import cache

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicFormat,
    SchematicLimits,
    SimulationResult,
    VersionLossEntry,
)
from squid.schematics.errors import SchematicSupportUnavailableError

ENGINE_MODULE = "nucleation"


@cache
def engine_installed() -> bool:
    """Return whether the native schematic engine is importable.

    Uses :func:`importlib.util.find_spec` rather than a trial import on purpose: importing a
    native extension is expensive, and an ABI-mismatched wheel can abort the interpreter
    outright. The real import happens only inside the worker subprocess, where a failure is
    contained and reported back as a capability answer.
    """
    try:
        return importlib.util.find_spec(ENGINE_MODULE) is not None
    except (ImportError, ValueError):
        # A shadowed or half-installed distribution can leave find_spec raising rather than
        # returning None; treat that as "not usable" instead of taking the bot down.
        return False


class NullSchematicAnalyzer:
    """The analyzer used when the engine is absent or switched off.

    Every operation raises the same typed, translated error, so an instance without the
    optional extra behaves exactly like today's bot plus one clear message, rather than
    failing somewhere deep with an `ImportError`.
    """

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason

    async def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(available=False, unavailable_reason=self._reason)

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        raise self._unavailable()

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        raise self._unavailable()

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        raise self._unavailable()

    async def render(self, data: bytes, *, request: RenderRequest, resource_pack: bytes | None = None) -> bytes:
        raise self._unavailable()

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        raise self._unavailable()

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        raise self._unavailable()

    async def aclose(self) -> None:
        """Nothing to release; present so callers can treat every analyzer the same way."""

    def _unavailable(self) -> SchematicSupportUnavailableError:
        return SchematicSupportUnavailableError(developer_action=self._reason)
