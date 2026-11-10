"""Run native analysis in threads for development and tests.

Calls share the application's process and default executor. Cancelling an await does not stop
the running thread, and a native process abort can terminate the application. Render and
simulation operations are refused here; use the subprocess pool for them and for production.
"""

import asyncio
import logging

from squid.config import SchematicConfig
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
from squid.schematics.errors import SchematicRenderUnavailableError, SchematicSupportUnavailableError

# Load the optional native engine only when a schematic operation needs it.
lazy from squid.schematics.infrastructure import nucleation_adapter as engine

logger = logging.getLogger(__name__)


class ThreadedSchematicAnalyzer:
    """Run engine calls on the default executor, in-process."""

    def __init__(self, config: SchematicConfig) -> None:
        self._config = config
        logger.warning(
            "Using the in-process schematic analyzer. An engine crash will take this process "
            "down with it; use the subprocess pool outside development."
        )

    async def capabilities(self) -> AnalyzerCapabilities:
        reported = await asyncio.to_thread(engine.capabilities)
        return AnalyzerCapabilities(
            available=reported.available,
            analyzer_version=reported.analyzer_version,
            can_render=False,
            can_simulate=False,
            unavailable_reason="The in-process analyzer refuses render and simulate.",
        )

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        return await asyncio.to_thread(
            engine.analyze,
            data,
            limits=limits,
            with_lattice=with_lattice,
            source_format=source_format,
            lattice_max_block_count=self._config.lattice_max_block_count,
        )

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        return await asyncio.to_thread(engine.convert, data, target=target, data_version=data_version)

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        """Compare in a thread; timeout_seconds is ignored because the native call cannot be stopped."""
        return await asyncio.to_thread(engine.compare, left, right, preset=preset)

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        return await asyncio.to_thread(engine.autostack, data, lattice=lattice, counts=counts)

    async def render(
        self, data: bytes, *, request: RenderRequest, resource_pack: VerifiedResourcePack | None = None
    ) -> bytes:
        msg = "The in-process schematic analyzer will not render."
        raise SchematicRenderUnavailableError(
            msg, developer_action="Rendering needs the subprocess pool; wgpu state is process-global."
        )

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        msg = "The in-process schematic analyzer will not simulate."
        raise SchematicSupportUnavailableError(
            msg, developer_action="Simulation needs the subprocess pool for cancellation and crash isolation."
        )

    async def aclose(self) -> None:
        """Nothing to release; present so callers can treat every analyzer the same way."""
