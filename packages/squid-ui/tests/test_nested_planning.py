"""Public planning traverses nested semantic containers."""

import squid_ui as sl
from squid_ui import scene
from squid_ui.ext.discord.planning import components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile


def test_container_interiors_are_planned() -> None:
    document = sl.section(
        sl.heading("Report"),
        sl.table(
            sl.columns(sl.column("Name"), sl.column("Score")),
            sl.table_row("Ada", "10"),
            key="scores",
        ),
    )
    result = plan(document, target=components_v2_target(AdapterProfile(DiscordAdapter, "nested-tests", "1")))
    wire = scene.Codec.dumps(result.scene)
    assert "Report" in wire
    assert "Ada" in wire and "10" in wire
