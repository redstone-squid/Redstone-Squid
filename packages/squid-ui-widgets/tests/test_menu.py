"""Menu: drilling into entries, and the chrome the machine owns on the way back out."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui_widgets import testing as wt


class Screen(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return sl.paragraph(f"screen: {self.name}")


def _settings() -> sp.Menu[sl.ComponentsV2Target]:
    # V2 throughout because one entry embeds `Screen`, which is a V2 component. Naming the
    # element type lets the portable entries solve to the same dialect instead of defaulting.
    entries: list[sp.MenuEntry[sl.ComponentsV2Target]] = [
        sp.MenuEntry("appearance", "Appearance", sl.paragraph("appearance body")),
        sp.MenuEntry(
            "Administration",
            Screen("administration"),
            entries=[sp.MenuEntry("Audit", sl.paragraph("audit body"), key="audit")],
        ),
    ]
    return sp.Menu("Settings", entries, key="settings")


async def test_the_root_shows_only_the_heading_and_ends_with_its_own_chrome() -> None:
    harness = wt.mounted(_settings())

    assert harness.texts() == ["Settings"], "`## ` is Discord markdown; the machine emits a Heading"
    assert harness.labels()[-3:] == ["Back", "Home", "Close"]


async def test_drilling_in_renders_the_entry_and_offers_its_children() -> None:
    harness = wt.mounted(_settings())

    await harness.press("settings.administration")

    assert harness.state == sp.MenuState(("administration",))
    assert "screen: administration" in harness.texts()
    assert "Audit" in harness.labels()


async def test_the_path_deepens_on_the_way_in_and_home_empties_it() -> None:
    harness = wt.mounted(_settings())

    await harness.press("settings.administration")
    await harness.press("settings.audit")

    assert harness.state == sp.MenuState(("administration", "audit"))

    await harness.press("settings.home")

    assert harness.state == sp.MenuState()


async def test_closing_finishes_the_shell() -> None:
    """Close-finishes-the-shell is wired by `Menu.build_component`, not by the bare driver."""
    harness = wt.driving(_settings().build_component())

    await harness.press("settings.close")

    assert harness.finished


def test_an_entry_derives_its_key_from_its_label_and_duplicates_are_refused() -> None:
    assert sp.MenuEntry("Appearance", sl.paragraph("body")).key == "appearance"

    with pytest.raises(ValueError, match="keys must be unique"):
        sp.Menu(
            "Settings",
            [sp.MenuEntry("Same", sl.paragraph("one")), sp.MenuEntry("Same", sl.paragraph("two"))],
        )
