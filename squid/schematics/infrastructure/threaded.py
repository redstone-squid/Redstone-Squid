"""A thread-backed analyzer for development and tests.

This runs the engine **in the bot's own process**, which is exactly what
:mod:`squid.schematics.infrastructure.worker` exists to avoid: there is no cancellation, and a
Rust panic terminates the interpreter. It earns its place by making unit and integration tests
readable — no subprocess, no framing, no respawn timing to reason about — and by giving a
developer a one-line way to bypass the supervisor while debugging the adapter itself.

`render` and `simulate` deliberately refuse. Those are the two operations whose failure modes
motivated process isolation in the first place: wgpu keeps process-global state that is not
fork-safe, and the simulator can spin unboundedly.
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
from squid.schematics.errors import SchematicRenderUnavailableError, SchematicSupportUnavailableError

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
        from squid.schematics.infrastructure import nucleation_adapter as engine

        reported = await asyncio.to_thread(engine.capabilities)
        # Advertised honestly: the engine can render, but this analyzer will not let it.
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
        from squid.schematics.infrastructure import nucleation_adapter as engine

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
        from squid.schematics.infrastructure import nucleation_adapter as engine

        return await asyncio.to_thread(engine.convert, data, target=target, data_version=data_version)

    async def compare(self, left: bytes, right: bytes, *, preset: FingerprintPreset) -> SchematicComparison:
        from squid.schematics.infrastructure import nucleation_adapter as engine

        return await asyncio.to_thread(engine.compare, left, right, preset=preset)

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        from squid.schematics.infrastructure import nucleation_adapter as engine

        return await asyncio.to_thread(engine.autostack, data, lattice=lattice, counts=counts)

    async def render(self, data: bytes, *, request: RenderRequest, resource_pack: bytes | None = None) -> bytes:
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
