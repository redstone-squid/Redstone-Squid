import pytest

from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterCapability
from squid_ui.primitives import Text
from squid_ui_discord.adapter import (
    discord_py_adapter_profile,
    require_discord_py_capability,
)
from squid_ui_discord.target import v2


def test_discord_py_boundary_rejects_unmatched_version() -> None:
    profile = discord_py_adapter_profile("future", ">=99", capabilities=frozenset({AdapterCapability.RENDER_V2}))

    with pytest.raises(LayoutInvariantError, match="supply a custom profile verified for this version"):
        require_discord_py_capability(profile, AdapterCapability.RENDER_V2, "render Components V2")


def test_discord_py_boundary_names_a_missing_operation_capability() -> None:
    profile = discord_py_adapter_profile("render-only", ">=2.7,<3", capabilities=frozenset())

    with pytest.raises(LayoutInvariantError, match=AdapterCapability.RENDER_V2):
        require_discord_py_capability(profile, AdapterCapability.RENDER_V2, "render Components V2")


def test_scene_body_can_be_narrowed_after_a_broad_decode() -> None:
    document = plan(Text("hello"), target=v2()).scene

    assert document.expect_body(scene.ComponentsV2) is document.body
    with pytest.raises(LayoutInvariantError, match="not ClassicMessage"):
        document.expect_body(scene.ClassicMessage)
