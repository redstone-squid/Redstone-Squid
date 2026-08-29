# pyright: reportPrivateUsage=false
"""Text rendered by the `/build schematic` and `/build measure-timing` commands."""

from squid.bot.submission.schematics import _CANDIDATE_LIMIT, _describe, _describe_input_refusal
from squid.core.i18n import tr
from squid.schematics.application import RenderSkipReason, SchematicPublication, StoredSchematic
from squid.schematics.errors import AmbiguousSimulationInputError
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
        assert not any(word in tr(reason.description).lower() for word in forbidden), reason


def test_an_ambiguous_input_lists_the_coordinates_to_choose_between() -> None:
    """Telling a moderator to pass coordinates without saying which ones is unactionable."""
    error = AmbiguousSimulationInputError(candidates=[(12, 5, -3), (0, 1, 2)])

    body = _describe_input_refusal(error, locale=None)

    assert "several possible inputs" in body
    assert "- `0 1 2`" in body
    assert "- `12 5 -3`" in body


def test_a_rejected_manual_coordinate_lists_the_accepted_ones() -> None:
    error = AmbiguousSimulationInputError(candidates=[(0, 1, 2)], rejected=(9, 9, 9))

    body = _describe_input_refusal(error, locale=None)

    assert "not a lever or button" in body
    assert "- `0 1 2`" in body


def test_a_schematic_with_no_control_at_all_lists_nothing() -> None:
    error = AmbiguousSimulationInputError()

    body = _describe_input_refusal(error, locale=None)

    assert "no input annotation and no lever or button" in body
    assert "Inputs found" not in body


def test_a_control_covered_wall_is_summarised_rather_than_dumped() -> None:
    candidates = [(x, 0, 0) for x in range(_CANDIDATE_LIMIT + 5)]

    body = _describe_input_refusal(AmbiguousSimulationInputError(candidates=candidates), locale=None)

    assert body.count("\n- ") == _CANDIDATE_LIMIT
    assert "…and 5 more not listed." in body
