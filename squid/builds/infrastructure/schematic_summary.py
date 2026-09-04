"""Adapter supplying `builds` with schematic facts from the schematic context.

Lives on the `builds` side of the seam on purpose: `builds` declares the port it wants, and
this module is the only place that knows both vocabularies. The schematic context has no idea
builds exist.
"""

from squid.builds.application.ports import BuildSchematicSummary
from squid.schematics.application import SchematicService


class SchematicServiceSummaryProvider:
    """Map a build's featured schematic into the narrow summary `builds` asked for."""

    def __init__(self, schematics: SchematicService) -> None:
        self._schematics = schematics

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None:
        """Return the featured schematic's facts, or `None` when there is nothing to show.

        An instance without the engine installed has no schematics at all, so the `None` path
        is the normal one there rather than an error.
        """
        if not self._schematics.available:
            return None

        stored = await self._schematics.featured_for_build(build_id)
        if stored is None:
            return None

        metrics = stored.analysis.metrics
        lattice = stored.analysis.lattice
        return BuildSchematicSummary(
            width=metrics.dimensions.width,
            height=metrics.dimensions.height,
            length=metrics.dimensions.length,
            block_count=metrics.block_count,
            palette_size=metrics.palette_size,
            source_data_version=metrics.source_data_version,
            lattice_label=lattice.label if lattice is not None else None,
            sign_texts=tuple(sign.text for sign in metrics.signs),
        )
