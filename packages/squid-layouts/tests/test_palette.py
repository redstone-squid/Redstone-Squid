"""Named palette selection."""

import pytest

from squid_layouts import Palette, PaletteRegistry


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
