"""Palette registry behavior at the live-mount boundary."""

import squid_ui as sl
from squid_ui_discord import MessageRoot, Owner


class Panel(sl.Component):
    def render(self) -> tuple[sl.LayoutNode, ...]:
        return (sl.section(sl.heading("Panel")),)


def test_registry_changes_do_not_retheme_a_live_root() -> None:
    original = sl.Palette(brand=0x111111)
    replacement = sl.Palette(brand=0x222222)
    registry = sl.PaletteRegistry({"squid": original}, default="squid")
    message_root = MessageRoot(Panel(), access=Owner(7), palette=registry.resolve())

    registry.register("squid", replacement)

    assert message_root.palette is original
    message_root.use_palette(registry.resolve())
    assert message_root.palette is replacement
