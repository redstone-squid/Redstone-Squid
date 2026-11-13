"""Named palette selection."""

import pytest

import squid_ui as sl
from squid_ui import Palette, PaletteRegistry
from squid_ui.planning.structure import resolve_accent


def test_registry_copies_its_input_and_resolves_the_default() -> None:
    original = Palette(brand=0x111111)
    source = {"first": original}
    registry = PaletteRegistry(source, default="first")

    source["first"] = Palette(brand=0x222222)

    assert registry.resolve() is original


def test_register_replaces_a_named_palette_and_default_can_switch() -> None:
    first = Palette(brand=0x111111)
    replacement = Palette(brand=0x222222)
    second = Palette(brand=0x333333)
    registry = PaletteRegistry({"first": first}, default="first")

    registry.register("first", replacement)
    registry.register("second", second)
    registry.set_default("second")

    assert registry.resolve("first") is replacement
    assert registry.resolve() is second


@pytest.mark.parametrize("name", ["", "missing"])
def test_registry_rejects_an_invalid_default(name: str) -> None:
    error = ValueError if not name else KeyError
    with pytest.raises(error):
        PaletteRegistry({"first": Palette()}, default=name)


def test_registry_rejects_empty_registration_and_unknown_resolution() -> None:
    registry = PaletteRegistry({"first": Palette()}, default="first")

    with pytest.raises(ValueError, match="must not be empty"):
        registry.register("", Palette())
    with pytest.raises(KeyError, match="unknown palette 'missing'"):
        registry.resolve("missing")
    with pytest.raises(KeyError, match="unknown palette 'missing'"):
        registry.set_default("missing")

    assert registry.resolve() == Palette()


@pytest.mark.parametrize("accent", [*sl.Tone, sl.AccentRole.BRAND, sl.INHERIT, None, 0x123456])
def test_accent_roles_follow_the_active_palette(accent: sl.Accent) -> None:
    palette = Palette(brand=1, neutral=2, info=3, success=4, warning=5, danger=6)
    expected: dict[sl.Accent, int | None] = {
        sl.Tone.NEUTRAL: 2,
        sl.Tone.INFO: 3,
        sl.Tone.SUCCESS: 4,
        sl.Tone.WARNING: 5,
        sl.Tone.DANGER: 6,
        sl.AccentRole.BRAND: 1,
        sl.INHERIT: 1,
        None: None,
        0x123456: 0x123456,
    }
    assert resolve_accent(accent, palette) == expected[accent]
    if isinstance(accent, sl.Tone):
        assert resolve_accent(accent, Palette(neutral=None, info=None, success=None, warning=None, danger=None)) is None


def test_custom_roles_resolve_overrides_fallbacks_and_explicit_absence() -> None:
    pending = sl.Role("build.pending", base=sl.Tone.WARNING, marker="⏳ ")
    overrides: dict[str, int | None] = {pending.name: 0x123456}
    palette = sl.Palette(warning=0x654321, roles=overrides)
    overrides[pending.name] = 0

    assert resolve_accent(pending, palette) == 0x123456
    assert sl.Palette(warning=0x654321).tone(pending) == 0x654321
    assert sl.Palette(roles={pending.name: None}).tone(pending) is None
    assert sl.Palette(roles={pending.name: 0}).tone(pending) == 0


@pytest.mark.parametrize("marker, expected", [(None, "⚠️ "), ("", ""), ("⏳ ", "⏳ ")])
def test_role_status_marker_can_inherit_replace_or_suppress(marker: str | None, expected: str) -> None:
    assert sl.Role("build.pending", sl.Tone.WARNING, marker).status_marker == expected
