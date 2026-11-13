"""Browsing through mounted Discord actions."""

import squid_ui_widgets as sp
from squid_ui.sources import list_source
from squid_ui_discord.testing import stage


async def test_detail_steps_cross_windows_and_return_to_the_containing_overview() -> None:
    browser = sp.Browser(
        list_source(("a", "b", "c")), identity=str, label=str, detail=lambda item: f"Detail {item}", page_size=2
    )
    async with stage(browser, owner=object()) as ui:
        await ui.press(ui.key_for("browser.open.b"))
        assert "Detail b" in ui.texts()
        await ui.press(ui.key_for("browser.item-next"))
        assert "Detail c" in ui.texts()
        await ui.press(ui.key_for("browser.item-previous"))
        assert "Detail b" in ui.texts()
        await ui.press(ui.key_for("browser.back"))
        assert "a" in " ".join(ui.texts())
