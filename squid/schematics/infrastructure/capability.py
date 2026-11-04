"""Detect the optional native engine without importing it into the application process."""

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
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import SchematicSupportUnavailableError

ENGINE_MODULE = "nucleation"


@cache
def engine_installed() -> bool:
    """Return whether Python can locate the native engine module, caching the result.

    find_spec avoids loading the extension here. A discoverable module can still fail to import
    or initialize; the subprocess adapter contains those failures when it checks capabilities.
    """
    try:
        return importlib.util.find_spec(ENGINE_MODULE) is not None
    except ImportError, ValueError:
        # A shadowed or half-installed distribution can leave find_spec raising rather than
        # returning None; treat that as "not usable" instead of taking the bot down.
        return False


class NullSchematicAnalyzer:
    """Represent an absent or disabled engine through the analyzer interface.

    Capabilities report unavailable, schematic operations raise SchematicSupportUnavailableError,
    and closing the adapter does nothing.
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

    async def render(
        self, data: bytes, *, request: RenderRequest, resource_pack: VerifiedResourcePack | None = None
    ) -> bytes:
        raise self._unavailable()

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        raise self._unavailable()

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        raise self._unavailable()

    async def aclose(self) -> None:
        """Nothing to release; present so callers can treat every analyzer the same way."""

    def _unavailable(self) -> SchematicSupportUnavailableError:
        return SchematicSupportUnavailableError(developer_action=self._reason)
