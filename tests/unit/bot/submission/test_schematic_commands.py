"""Text rendered by the `/build schematic` and `/build measure-timing` commands."""

from squid.bot.submission.schematics import _describe
from squid.schematics.application import RenderSkipReason, SchematicPublication, StoredSchematic
from tests.unit.schematics.fakes import make_analysis


def stored(**kwargs: object) -> StoredSchematic:
    return StoredSchematic(
        id=1,
        build_id=7,
        file_sha256="0" * 64,
        is_primary=True,
        original_filename="door.litematic",
        analysis=make_analysis(),
        publication=SchematicPublication(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_info_card_says_nothing_about_previews_when_one_is_possible() -> None:
    body = _describe(stored(), locale=None, render_skip=None)

    assert "No preview" not in body


def test_the_info_card_names_why_a_build_will_never_get_a_preview() -> None:
    """ "No preview here" and "no preview yet" call for different moderator responses."""
    body = _describe(stored(), locale=None, render_skip=RenderSkipReason.NOT_SANITIZED)

    assert "**No preview**: This schematic has not been sanitized, so it is never rendered." in body


def test_no_skip_reason_leaks_the_engine_or_its_configured_caps() -> None:
    forbidden = ("nucleation", "wgpu", "vulkan", "worker", "adapter", "block_count", "bounding_volume")

    for reason in RenderSkipReason:
        assert not any(word in reason.description.lower() for word in forbidden), reason
