"""Optional page-size forms obey browser limits and refresh the visible batch."""

import discord
import pytest

from squid_ui.ext.discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Everyone, MessageRoot
from squid_ui.ext.discord.navigation_controls import page_jump_nav
from squid_ui.ext.discord.testing import delivered_to, interaction_harness, iter_component_payloads, message_harness
from squid_ui.planning.target import Target
from squid_ui.sources import list_source
from squid_ui_widgets import Browser


@pytest.mark.parametrize("target", [DISCORD_V1_DPY27, DISCORD_V2_DPY27])
@pytest.mark.parametrize("value", ["1", "25", "0", "26", "-1", "1.5", "no"])
async def test_browser_fetch_size_form_validates_and_reloads(target: Target, value: str) -> None:
    browser = Browser(
        list_source(tuple(str(index) for index in range(30)), fixed_groups=True),
        identity=str,
        label=str,
        detail=str,
        fetch_size=10,
        show_fetch_size=True,
    )
    root = MessageRoot(browser, access=Everyone(), timeout=None, target=target, nav=page_jump_nav)
    await root.send(delivered_to(message_harness(components_v2=target == DISCORD_V2_DPY27)))
    view = root._candidates.view
    assert view is not None
    assert ["Previous", "Next", "Jump to a page", "Items per page"] in [
        [item.get("label") for item in row["components"]]
        for row in iter_component_payloads(view.to_components())
        if row["type"] == discord.ComponentType.action_row.value
    ]
    interaction = interaction_harness(components_v2=target == DISCORD_V2_DPY27)
    await root.dispatch("browser.fetch-size", interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    assert modal.children[0].component.default == "10"
    modal.children[0].component._value = value
    submitted = interaction_harness(components_v2=target == DISCORD_V2_DPY27)
    await modal.on_submit(submitted)
    expected = int(value) if value in ("1", "25") else 10
    assert browser.fetch_size == expected
    assert browser.session.snapshot is not None
    assert browser.session.snapshot.loaded.batch.items == tuple(str(index) for index in range(expected))
    if expected == 10:
        assert submitted.response.send_message.await_args.kwargs["ephemeral"]
