"""Public build-card measurements use stored analysis and attachment identity."""

from dataclasses import replace

import pytest
from whenever import Instant

from squid.builds.infrastructure.schematic_summary import SchematicServiceSummaryProvider
from squid.schematics.application import SchematicPublication, SchematicService, StoredSchematic
from squid.schematics.domain.models import SchematicLicense, SchematicVisibility
from tests.unit.schematics.fakes import FakeSchematicAnalyzer, FakeSchematicStore, FakeVersionResolver, make_analysis


def public_attachment() -> StoredSchematic:
    now = Instant.now()
    return StoredSchematic(
        id=12,
        build_id=7,
        file_sha256="0" * 64,
        is_primary=True,
        original_filename="door.litematic",
        analysis=make_analysis(),
        publication=SchematicPublication(
            visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
            license=SchematicLicense.CC0_1_0,
            rights_attested_at=now,
            rights_attested_by_account_id=1,
            published_at=now,
        ),
    )


async def test_summary_tracks_featured_file_and_withdrawal_without_engine() -> None:
    analyzer = FakeSchematicAnalyzer()
    store = FakeSchematicStore()
    schematics = SchematicService(analyzer, store, store, FakeVersionResolver(), engine_installed=False)
    provider = SchematicServiceSummaryProvider(schematics, public_base_url="https://api.example.com/")
    first = public_attachment()
    store.stored = [first]
    summary = await provider.summary_for(7)
    assert summary is not None
    assert (summary.width, summary.height, summary.length) == (3, 4, 5)
    assert summary.download_url == "https://api.example.com/v1/builds/7/schematics/12/content"
    assert not summary.multiple_files
    second = replace(first, id=13, file_sha256="1" * 64, analysis=make_analysis(dimensions=(8, 9, 10)))
    store.stored = [replace(first, is_primary=False), second]
    summary = await provider.summary_for(7)
    assert summary is not None
    assert (summary.width, summary.height, summary.length) == (8, 9, 10)
    assert summary.download_url.endswith("/13/content")
    assert summary.multiple_files
    store.stored[1] = replace(second, publication=replace(second.publication, withdrawn_at=Instant.now()))
    assert await provider.summary_for(7) is None
    assert analyzer.analyze_calls == []


@pytest.mark.parametrize("kind", ["absent", "private", "unpublished", "secondary", "no_origin"])
async def test_ineligible_schematics_have_no_card_summary(kind: str) -> None:
    store = FakeSchematicStore()
    attachment = public_attachment()
    if kind == "private":
        attachment = replace(attachment, publication=SchematicPublication())
    elif kind == "unpublished":
        attachment = replace(attachment, publication=replace(attachment.publication, published_at=None))
    elif kind == "secondary":
        attachment = replace(attachment, is_primary=False)
    store.stored = [] if kind == "absent" else [attachment]
    schematics = SchematicService(FakeSchematicAnalyzer(), store, store, FakeVersionResolver())
    provider = SchematicServiceSummaryProvider(
        schematics,
        public_base_url=None if kind == "no_origin" else "https://api.example.com",
    )
    assert await provider.summary_for(7) is None
