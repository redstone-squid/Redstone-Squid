"""Palette registry behavior at the live-mount boundary."""

import squid_layouts as sl
from squid_discord import Mount, Owner


class Panel(sl.Component):
    def render(self) -> tuple[sl.LayoutNode, ...]:
        return (sl.section(sl.heading("Panel")),)


def test_registry_changes_do_not_retheme_a_live_mount() -> None:
    original = sl.Palette(brand=0x111111)
    replacement = sl.Palette(brand=0x222222)
    registry = sl.PaletteRegistry({"squid": original}, default="squid")
    mount = Mount(Panel(), access=Owner(7), palette=registry.resolve())

    registry.register("squid", replacement)

    assert mount.palette is original
    mount.use_palette(registry.resolve())
    assert mount.palette is replacement
