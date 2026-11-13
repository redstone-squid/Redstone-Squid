"""Select public schematic measurements without invoking the native engine."""

from squid.builds.application.ports import BuildSchematicSummary
from squid.schematics.application import SchematicService


class SchematicServiceSummaryProvider:
    """Read the publicly downloadable featured attachment; callers check build access."""

    def __init__(self, schematics: SchematicService, *, public_base_url: str | None) -> None:
        self._schematics = schematics
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None

    async def summary_for(self, build_id: int) -> BuildSchematicSummary | None:
        """Return saved facts and an attachment-specific URL, or None when unavailable."""
        if self._public_base_url is None:
            return None
        attachments = await self._schematics.list_public_for_build(build_id)
        stored = next((attachment for attachment in attachments if attachment.is_primary), None)
        if stored is None:
            return None
        dimensions = stored.analysis.metrics.dimensions
        return BuildSchematicSummary(
            width=dimensions.width,
            height=dimensions.height,
            length=dimensions.length,
            schematic_id=stored.id,
            download_url=f"{self._public_base_url}/v1/builds/{build_id}/schematics/{stored.id}/content",
            filename=stored.original_filename,
            multiple_files=len(attachments) > 1,
        )
