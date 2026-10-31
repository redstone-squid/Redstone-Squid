from dataclasses import FrozenInstanceError

import pytest

from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import PreparedExtension, ResourceCost
from squid_ui.planning.types import DiscordAdapter


class AlternateAdapter(DiscordAdapter):
    pass


class ExampleExtension:
    def prepare(self, payload: object) -> PreparedExtension:
        return PreparedExtension(ResourceCost(), {}, payload)


def test_adapter_profile_freezes_capabilities_and_extensions() -> None:
    capabilities = {AdapterCapability.DISPATCH}
    extensions = {"example": ExampleExtension()}
    profile = AdapterProfile(
        AlternateAdapter,
        "example",
        ">=1,<2",
        frozenset(capabilities),
        extensions,
    )

    capabilities.add(AdapterCapability.RENDER_HTML)
    extensions["later"] = ExampleExtension()

    assert profile.capabilities == frozenset({"adapter.discord.dispatch"})
    assert tuple(profile.extensions) == ("example",)
    assert profile.extension_capabilities == frozenset({"extension.example"})
    assert profile.combine_capabilities(frozenset({"message.content"})) == frozenset(
        {"message.content", "adapter.discord.dispatch", "extension.example"}
    )
    with pytest.raises(TypeError):
        profile.extensions["new"] = ExampleExtension()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        profile.name = "changed"  # type: ignore[misc]


def test_resource_cost_values_are_immutable() -> None:
    cost = ResourceCost()

    with pytest.raises(TypeError):
        cost.values[Axis.COMPONENTS] = 1  # type: ignore[index]


@pytest.mark.parametrize("name, expression", [("", ">=1"), ("example", "")])
def test_adapter_profile_requires_stable_identity(name: str, expression: str) -> None:
    with pytest.raises(ValueError):
        AdapterProfile(AlternateAdapter, name, expression)
