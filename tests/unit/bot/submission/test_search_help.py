"""The runnable reference examples must be accepted by the real query parser."""

import pytest

from squid.bot.submission.search_help import BUILD_EXAMPLES, RECORD_EXAMPLES, _entry, _reference
from squid.search.application import SearchQueryParser
from squid_ui.errors import UnsolvableLayoutError
from squid_ui.planning import plan
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui_discord import DISCORD_V2_DPY27
from squid_ui_discord.rendering import render_static
from squid_ui_discord.testing import payload_texts


@pytest.mark.parametrize("query", [query for _label, query in (*BUILD_EXAMPLES, *RECORD_EXAMPLES)])
def test_help_examples_are_valid_queries(query: str) -> None:
    assert SearchQueryParser().parse(query).expression is not None


def test_reference_entry_compacts_without_losing_its_label_or_description() -> None:
    entry = _reference((_entry("width", "Physical width in blocks."), _entry("height", "Height in blocks.")))
    normal = plan(entry, target=DISCORD_V2_DPY27)
    compact_target = DISCORD_V2_DPY27.reserve(ResourceCost({Axis.COMPONENTS: 39}))
    compact = plan(entry, target=compact_target)

    assert normal.scene != compact.scene
    view = render_static(entry, target=compact_target).view
    assert view is not None
    assert payload_texts(view) == ["width: Physical width in blocks.\n\nheight: Height in blocks."]
    assert not normal.report.events


def test_reference_entry_fails_when_neither_representation_fits() -> None:
    target = DISCORD_V2_DPY27.reserve(ResourceCost({Axis.COMPONENTS: 40}))
    with pytest.raises(UnsolvableLayoutError):
        plan(_reference((_entry("width", "Physical width in blocks."),)), target=target)
