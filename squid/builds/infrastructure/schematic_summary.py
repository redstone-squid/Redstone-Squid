"""Select schematic measurements for build cards without exposing the full analysis model."""

from squid.builds.application.ports import BuildSchematicSummary
from squid.schematics.application import SchematicService


class SchematicServiceSummaryProvider:
    """Read a build's featured schematic and select the facts used on its card."""

    def __init__(self, schematics: SchematicService) -> None:
        self._schematics = schematics

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None:
        """Return featured schematic facts, or None if none are available.

        An unavailable schematic engine also returns None.
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
