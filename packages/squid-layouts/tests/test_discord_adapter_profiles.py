import subprocess
import sys

import pytest

from squid_layouts import plan
from squid_layouts.discord import DISCORD_PY_27_ADAPTER, Target
from squid_layouts.discord.adapter import discord_py_adapter_profile, require_discord_py_capability
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.adapter import ADAPTER_RENDER_V2, AdapterProfile
from squid_layouts.planning.discord import classic_target, components_v2_target
from squid_layouts.planning.types import DiscordAdapter
from squid_layouts.primitives import Text
from squid_layouts.scene.model import SceneComponentsV2


class AlternateAdapter(DiscordAdapter):
    pass


def test_builtin_targets_bind_the_verified_discord_py_profile() -> None:
    assert Target.v2().adapter is DISCORD_PY_27_ADAPTER
    assert Target.classic().adapter is DISCORD_PY_27_ADAPTER
    assert ADAPTER_RENDER_V2 in Target.v2().capabilities
    assert "extension.discord.item" in Target.v2().capabilities
    assert "extension.discord.item" not in Target.classic().capabilities


def test_alternate_profile_can_plan_for_an_injected_renderer() -> None:
    profile = AdapterProfile(AlternateAdapter, "alternate", ">=1", frozenset({"alternate.render"}))
    target = components_v2_target(profile)

    scene = plan(Text("hello"), target=target).scene

    assert isinstance(scene.body, SceneComponentsV2)
    assert target.adapter is profile
    assert "alternate.render" in target.capabilities


def test_protocol_target_import_does_not_load_discord_py() -> None:
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'discord' or name.startswith('discord.'):
        raise AssertionError(f'discord.py imported by {name}')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.discord import components_v2_target
from squid_layouts.planning.types import DiscordAdapter
components_v2_target(AdapterProfile(DiscordAdapter, 'alternate', '>=1'))
"""

    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr


def test_discord_py_boundary_accepts_installed_27_release() -> None:
    require_discord_py_capability(DISCORD_PY_27_ADAPTER, ADAPTER_RENDER_V2, "render Components V2")


def test_discord_py_boundary_rejects_unmatched_version() -> None:
    profile = discord_py_adapter_profile("future", ">=99", capabilities=frozenset({ADAPTER_RENDER_V2}))

    with pytest.raises(LayoutInvariantError, match="supply a custom profile verified for this version"):
        require_discord_py_capability(profile, ADAPTER_RENDER_V2, "render Components V2")


def test_discord_py_boundary_names_a_missing_operation_capability() -> None:
    profile = discord_py_adapter_profile("render-only", ">=2.7,<3", capabilities=frozenset())

    with pytest.raises(LayoutInvariantError, match=ADAPTER_RENDER_V2):
        require_discord_py_capability(profile, ADAPTER_RENDER_V2, "render Components V2")


def test_protocol_factories_union_only_applicable_extension_capabilities() -> None:
    assert "extension.discord.item" in components_v2_target(DISCORD_PY_27_ADAPTER).capabilities
    assert "extension.discord.item" not in classic_target(DISCORD_PY_27_ADAPTER).capabilities
