"""Public schematic routes are attachment-scoped and publication-safe."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from whenever import Instant

from squid.api.v1.schematics import (
    get_schematic_content,
    list_build_schematics,
    render_build_schematic,
    router,
)
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.errors import BuildNotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, offset_page
from squid.schematics.application import (
    RenderedSchematic,
    RenderRequest,
    RenderSkipReason,
    SchematicPublication,
    SchematicService,
    StoredSchematic,
)
from squid.schematics.application.queries import PublicSchematicDownload
from squid.schematics.domain import SchematicFormat, SchematicLicense, SchematicVisibility
from squid.schematics.errors import SchematicRenderRefusedError
from tests.unit.schematics.fakes import make_analysis


def _public_publication() -> SchematicPublication:
    now = Instant.parse_iso("2026-08-11T12:00:00Z")
    return SchematicPublication(
        visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
        license=SchematicLicense.CC_BY_4_0,
        rights_attested_at=now,
        rights_attested_by_account_id=7,
        sanitized_at=now,
        sanitizer_version="nucleation-test",
        sanitization_report={"removed": 0},
        published_at=now,
    )


class ConfirmedBuilds:
    async def get_public(self, build_id: int) -> object:
        if build_id != 7:
            raise BuildNotFoundError(build_id)
        return SimpleNamespace(submission_status=Status.CONFIRMED)


class PublicSchematics:
    def __init__(self) -> None:
        self.stored = StoredSchematic(
            id=3,
            build_id=7,
            file_sha256="a" * 64,
            is_primary=True,
            original_filename="private-player-name.litematic",
            analysis=make_analysis(),
            publication=_public_publication(),
        )

    async def list_public_page(
        self,
        build_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 50,
    ) -> Page[StoredSchematic]:
        items = [self.stored] if build_id == 7 else []
        return offset_page(items, offset=selector.offset, page_size=page_size)

    async def public_download(self, build_id: int, schematic_id: int) -> PublicSchematicDownload:
        assert (build_id, schematic_id) == (7, 3)
        assert self.stored.publication.license is not None
        return PublicSchematicDownload(
            content=b"sanitized-sponge-v3",
            schematic=self.stored,
            license=self.stored.publication.license,
            source_format=self.stored.analysis.metrics.source_format,
        )


class RenderingSchematics(PublicSchematics):
    """Answer renders the way the service does: a configured recipe plus caller overrides."""

    def __init__(self, *, refusal: RenderSkipReason | None = None) -> None:
        super().__init__()
        self.configured = RenderRequest(width=1024, height=1024)
        self.rendered: list[RenderRequest] = []
        self._refusal = refusal

    def render_recipe(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
        zoom: float | None = None,
    ) -> RenderRequest:
        base = self.configured
        return replace(
            base,
            width=base.width if width is None else width,
            height=base.height if height is None else height,
            yaw=base.yaw if yaw is None else yaw,
            pitch=base.pitch if pitch is None else pitch,
            zoom=base.zoom if zoom is None else zoom,
        )

    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        assert build_id == 7
        if self._refusal is not None:
            raise SchematicRenderRefusedError(self._refusal.value, self._refusal.description)
        resolved = request or self.configured
        self.rendered.append(resolved)
        return RenderedSchematic(
            build_id=build_id,
            schematic_id=self.stored.id,
            recipe_hash="b" * 64,
            width=resolved.width,
            height=resolved.height,
            png=b"\x89PNG\r\n\x1a\nrendered",
            from_cache=False,
        )


async def test_a_render_with_no_parameters_asks_for_the_deployment_recipe() -> None:
    """Omitting every control has to hit the recipe the queue caches, not a route default."""
    schematics = RenderingSchematics()

    response = await render_build_schematic(
        7,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, schematics),
    )

    assert schematics.rendered == [schematics.configured]
    assert response.body == b"\x89PNG\r\n\x1a\nrendered"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == f'"{"b" * 64}"'
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_render_applies_only_the_controls_the_caller_named() -> None:
    schematics = RenderingSchematics()

    await render_build_schematic(
        7,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, schematics),
        yaw=45.0,
    )

    assert schematics.rendered[0].yaw == 45.0
    assert schematics.rendered[0].width == schematics.configured.width


async def test_a_render_refusal_names_the_reason_in_public_context() -> None:
    schematics = RenderingSchematics(refusal=RenderSkipReason.OVER_BLOCK_BUDGET)

    with pytest.raises(SchematicRenderRefusedError) as refusal:
        await render_build_schematic(
            7,
            cast(BuildQueryService, ConfirmedBuilds()),
            cast(SchematicService, schematics),
        )

    assert refusal.value.public_context == {"reason": "over_block_budget"}


async def test_public_metadata_omits_digest_and_original_filename() -> None:
    page = await list_build_schematics(
        7,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, PublicSchematics()),
        page_size=50,
        offset=None,
    )

    assert page.total == 1
    item = page.items[0].model_dump(mode="json")
    assert "sha256" not in item
    assert "filename" not in item
    assert item["license"] == "cc_by_4_0"
    assert item["download_url"] == "/v1/builds/7/schematics/3/content"


async def test_download_uses_scoped_locator_and_short_revalidation_cache() -> None:
    response = await get_schematic_content(
        7,
        3,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, PublicSchematics()),
    )

    assert response.body == b"sanitized-sponge-v3"
    assert response.headers["content-type"] == "application/octet-stream"
    # The fixture is a .litematic; every download used to be named `.schem` regardless
    # of the container that was actually stored.
    assert response.headers["content-disposition"] == 'attachment; filename="build-7-schematic-3.litematic"'
    assert "x-schematic-license" not in response.headers
    assert response.headers["cache-control"] == "public, max-age=300, must-revalidate"
    # The standard Link header carries the license instead of a bespoke X- header.
    assert 'rel="license"' in response.headers["link"]
    assert "creativecommons.org/licenses/by/4.0/" in response.headers["link"]
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/schematics/{sha256}/content" not in paths


def test_publication_value_is_not_forgeable_from_an_unsanitized_record() -> None:
    legacy = SchematicPublication()

    assert legacy.is_public_downloadable is False


@pytest.mark.parametrize("source_format", list(SchematicFormat))
async def test_each_stored_format_downloads_under_its_own_extension(source_format: SchematicFormat) -> None:
    """The extension follows the container the analysis recorded, so a `.litematic`
    stops arriving named `.schem`. The stem stays server-generated."""
    schematics = PublicSchematics()
    analysis = schematics.stored.analysis
    schematics.stored = replace(
        schematics.stored,
        analysis=replace(analysis, metrics=replace(analysis.metrics, source_format=source_format)),
    )

    response = await get_schematic_content(
        7,
        3,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, schematics),
    )

    expected = f'attachment; filename="build-7-schematic-3.{source_format.value}"'
    assert response.headers["content-disposition"] == expected
