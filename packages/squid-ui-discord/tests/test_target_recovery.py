"""A recovered mount is rebuilt against the exact target its render was fitted to."""

import json
import re

import pytest

import squid_ui_discord
import squid_ui as sl
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Everyone, Mount
from squid_ui_discord.adapter import discord_py_adapter_profile
from squid_ui_discord.durability import DEFAULT_TARGETS, ComponentRegistry, MountStateCodec
from squid_ui_discord.target import classic
from squid_ui_discord.targets import TargetRegistry
from squid_ui_discord.testing import commit_classic_render, commit_render
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import Target
from squid_ui.planning.adapter import (
    AdapterCapability,
)
from squid_ui.planning.limits import ClassicLimits


class Screen(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return sl.paragraph(f"count {self.count}")


def registry() -> ComponentRegistry:
    components = ComponentRegistry()
    components.register("screen", version=1, factory=Screen)
    return components


def captured(target: Target):
    components = registry()
    mount = Mount(Screen(), target=target, access=Everyone())
    commit(target, mount)
    return components, components.capture(mount, "screen")


def commit(target: Target, mount: Mount) -> None:
    if target.id == DISCORD_V2_DPY27.id:
        commit_render(mount)
    else:
        commit_classic_render(mount)


class TestFingerprints:
    def test_the_two_built_in_targets_never_share_a_fingerprint(self) -> None:
        assert DISCORD_V2_DPY27.fingerprint != DISCORD_V1_DPY27.fingerprint

    def test_tightening_a_limit_changes_the_fingerprint(self) -> None:
        """Because a stored render fitted to 10 embeds was never fitted to 2."""
        tightened = classic(limits=ClassicLimits(embed_count=2))

        assert tightened.fingerprint != DISCORD_V1_DPY27.fingerprint

    def test_two_constructions_of_the_same_profile_agree(self) -> None:
        assert classic().fingerprint == DISCORD_V1_DPY27.fingerprint


class TestSnapshot:
    @pytest.mark.parametrize("target", [DISCORD_V2_DPY27, DISCORD_V1_DPY27])
    def test_a_snapshot_records_the_target_it_was_planned_against(self, target) -> None:
        _components, snapshot = captured(target)

        assert snapshot.target_triple == target.triple
        assert snapshot.target_version == target.version
        assert snapshot.target_fingerprint == target.fingerprint
        assert snapshot.target_adapter_capabilities == tuple(sorted(target.adapter_capabilities))

    @pytest.mark.parametrize("target", [DISCORD_V2_DPY27, DISCORD_V1_DPY27])
    def test_the_target_survives_the_canonical_codec(self, target) -> None:
        _components, snapshot = captured(target)

        restored = MountStateCodec.loads(MountStateCodec.dumps(snapshot))

        assert restored == snapshot

    def test_the_former_protocol_two_shape_is_refused(self) -> None:
        _components, snapshot = captured(DISCORD_V2_DPY27)
        payload = MountStateCodec.dumps(snapshot).replace('"protocol":1', '"protocol":2', 1)

        with pytest.raises(squid_ui_discord.durability.MountStateError, match="unsupported mount state protocol 2"):
            MountStateCodec.loads(payload)

    def test_the_former_protocol_one_shape_without_adapter_capabilities_is_refused(self) -> None:
        _components, snapshot = captured(DISCORD_V2_DPY27)
        raw = json.loads(MountStateCodec.dumps(snapshot))
        raw["target"].pop("adapter_capabilities")
        payload = json.dumps(raw)

        with pytest.raises(squid_ui_discord.durability.MountStateError, match="adapter_capabilities"):
            MountStateCodec.loads(payload)


class TestRecovery:
    @pytest.mark.parametrize("target", [DISCORD_V2_DPY27, DISCORD_V1_DPY27])
    def test_recovery_rebuilds_the_mount_against_the_recorded_target(self, target) -> None:
        components, snapshot = captured(target)

        restored = components.restore(snapshot, access=Everyone())

        assert restored.target is target

    def test_an_unregistered_target_is_refused_by_name(self) -> None:
        components, snapshot = captured(DISCORD_V1_DPY27)
        empty = TargetRegistry(builtins=False)

        expected = re.escape("no target registered for 'discord.components-v1+discord.py'")
        with pytest.raises(LayoutInvariantError, match=expected):
            components.restore(snapshot, targets=empty, access=Everyone())

    def test_a_changed_profile_is_refused_rather_than_silently_substituted(self) -> None:
        """Rebuilding against different budgets would make the stored render legal by luck."""
        components, snapshot = captured(DISCORD_V1_DPY27)
        drifted = TargetRegistry(classic(limits=ClassicLimits(embed_count=2)), builtins=False)

        with pytest.raises(LayoutInvariantError, match="no longer matches the profile"):
            components.restore(snapshot, targets=drifted, access=Everyone())

    def test_a_custom_target_recovers_once_it_is_registered(self) -> None:
        custom = classic(limits=ClassicLimits(embed_count=2))
        components = registry()
        mount = Mount(Screen(), target=custom, access=Everyone())
        commit(custom, mount)
        snapshot = components.capture(mount, "screen")
        targets = TargetRegistry(custom, builtins=False)

        restored = components.restore(snapshot, targets=targets, access=Everyone())

        assert restored.target is custom

    def test_a_superset_adapter_recovers_with_the_recorded_planning_capabilities(self) -> None:
        mount_capabilities = frozenset(
            {AdapterCapability.RENDER_CLASSIC, AdapterCapability.DISPATCH, AdapterCapability.INTERACTION_DELIVERY}
        )
        old_profile = discord_py_adapter_profile("discord.py", ">=2.7,<3", capabilities=mount_capabilities)
        current_profile = discord_py_adapter_profile(
            "discord.py",
            ">=2.7,<3",
            capabilities=mount_capabilities | {AdapterCapability.MODAL_FORMS},
        )
        old_target = classic(adapter=old_profile)
        components, snapshot = captured(old_target)
        targets = TargetRegistry(classic(adapter=current_profile), builtins=False)

        restored = components.restore(snapshot, targets=targets, access=Everyone())

        assert restored.target.adapter_capabilities == mount_capabilities
        assert AdapterCapability.MODAL_FORMS not in restored.target.capabilities

    def test_recovery_rejects_a_missing_recorded_adapter_capability(self) -> None:
        mount_capabilities = frozenset(
            {AdapterCapability.RENDER_CLASSIC, AdapterCapability.DISPATCH, AdapterCapability.INTERACTION_DELIVERY}
        )
        old_profile = discord_py_adapter_profile(
            "discord.py",
            ">=2.7,<3",
            capabilities=mount_capabilities | {AdapterCapability.MODAL_FORMS},
        )
        current_profile = discord_py_adapter_profile("discord.py", ">=2.7,<3", capabilities=mount_capabilities)
        components, snapshot = captured(classic(adapter=old_profile))
        targets = TargetRegistry(classic(adapter=current_profile), builtins=False)

        with pytest.raises(LayoutInvariantError, match=AdapterCapability.MODAL_FORMS):
            components.restore(snapshot, targets=targets, access=Everyone())

    def test_the_built_in_targets_need_no_registration(self) -> None:
        assert DISCORD_V2_DPY27.triple in DEFAULT_TARGETS
        assert DISCORD_V1_DPY27.triple in DEFAULT_TARGETS

    def test_a_custom_profile_may_replace_a_built_in_under_its_own_triple(self) -> None:
        """`classic(limits=...)` keeps the built-in triple — still classic over discord.py."""
        compact = classic(limits=ClassicLimits(embed_count=2))

        assert TargetRegistry(compact).resolve(compact.triple, compact.version, compact.fingerprint) is compact

    def test_a_registered_protocol_with_an_unregistered_adapter_is_refused_by_name(self) -> None:
        """The collision the triple closes: an id alone would have resolved the wrong target.

        Both targets are classic messages at `discord.components-v1`; only the adapter
        differs. Keyed on the protocol id, the snapshot would have silently resolved to the
        other adapter's target and been rebuilt against budgets it was never fitted to.
        """
        elsewhere = discord_py_adapter_profile("elsewhere.py", ">=2.7,<3")
        components, snapshot = captured(classic(adapter=elsewhere))
        targets = TargetRegistry(builtins=True)

        expected = re.escape("no target registered for 'discord.components-v1+elsewhere.py'")
        with pytest.raises(LayoutInvariantError, match=expected):
            components.restore(snapshot, targets=targets, access=Everyone())

    def test_replacing_a_built_in_still_refuses_a_snapshot_planned_against_the_old_one(self) -> None:
        """Nothing is lost by allowing the override: the fingerprint does the real work."""
        components, snapshot = captured(DISCORD_V1_DPY27)
        targets = TargetRegistry(classic(limits=ClassicLimits(embed_count=2)))

        with pytest.raises(LayoutInvariantError, match="no longer matches the profile"):
            components.restore(snapshot, targets=targets, access=Everyone())

    def test_the_target_is_resolved_before_anything_is_built(self) -> None:
        """So a bad record fails while it is still just data, not once a reader can click it."""
        built: list[Screen] = []

        def factory() -> Screen:
            screen = Screen()
            built.append(screen)
            return screen

        components = ComponentRegistry()
        components.register("screen", version=1, factory=factory)
        mount = Mount(Screen(), target=DISCORD_V1_DPY27, access=Everyone())
        commit(DISCORD_V1_DPY27, mount)
        snapshot = components.capture(mount, "screen")
        built.clear()

        with pytest.raises(LayoutInvariantError):
            components.restore(snapshot, targets=TargetRegistry(builtins=False), access=Everyone())

        assert built == []
