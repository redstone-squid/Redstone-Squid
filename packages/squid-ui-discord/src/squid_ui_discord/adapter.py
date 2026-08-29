"""The verified discord.py adapter profile and boundary checks."""

from collections.abc import Callable, Mapping
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

import discord
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from squid_ui.errors import LayoutInvariantError
from squid_ui.extensions import ExtensionKind
from squid_ui.planning.adapter import (
    AdapterCapability,
    AdapterProfile,
    ExtensionAdapter,
    PreparedExtension,
)
from squid_ui.planning.target import AnyTarget
from squid_ui.target_types import DiscordPy27Adapter, DiscordPyAdapter
from squid_ui_discord.inspection import cost

type ItemFactory = Callable[[], discord.ui.Item[Any]]

DISCORD_ITEM = ExtensionKind[ItemFactory, discord.ui.Item[Any]]("discord.item")
"""Embed one discord.py item, measured, behind a portable fallback.

The pairing the kind string could not state: authored with a zero-argument factory,
drawn as the `discord.ui.Item` that factory returns.
"""


class _DiscordItemExtension:
    def prepare(self, payload: ItemFactory) -> PreparedExtension[discord.ui.Item[Any]]:
        # Still checked, though `ExtensionKind` now states it: a scene can be authored from
        # untyped code, and "not a factory" is a better report than "the factory failed".
        if not callable(payload):
            message = "discord.item extension payload must be a zero-argument factory"
            raise LayoutInvariantError(message)
        try:
            item = payload()
        except Exception as error:
            message = "discord.item factory failed during target planning"
            raise LayoutInvariantError(message) from error
        if not isinstance(item, discord.ui.Item):
            message = "discord.item factory did not return a discord.ui.Item"
            raise LayoutInvariantError(message)
        return PreparedExtension(cost(item), {"native_kind": type(item).__name__}, item)


DISCORD_PY_BEHAVIOR_CAPABILITIES = frozenset(AdapterCapability)

DISCORD_PY_27_ADAPTER = AdapterProfile(
    DiscordPy27Adapter,
    "discord.py",
    ">=2.7,<2.8",
    DISCORD_PY_BEHAVIOR_CAPABILITIES,
    {DISCORD_ITEM.name: _DiscordItemExtension()},
)


@cache
def _installed_discord_py() -> Version:
    return Version(version("discord.py"))


@cache
def _version_range(expression: str) -> SpecifierSet:
    return SpecifierSet(expression)


def discord_py_adapter_profile(
    name: str,
    version_expression: str,
    *,
    capabilities: frozenset[AdapterCapability] = DISCORD_PY_BEHAVIOR_CAPABILITIES,
    extensions: Mapping[str, ExtensionAdapter[Any, Any]] | None = None,
) -> AdapterProfile[DiscordPyAdapter]:
    """Declare an application-verified discord.py adapter profile."""
    return AdapterProfile(
        DiscordPyAdapter,
        name,
        version_expression,
        capabilities,
        {} if extensions is None else extensions,
    )


def require_discord_py_capability(
    profile: AdapterProfile[DiscordPyAdapter], capability: AdapterCapability, operation: str
) -> None:
    """Verify the selected profile and installed discord.py at an adapter boundary."""
    if capability not in profile.capabilities:
        message = f"adapter profile {profile.name!r} cannot {operation}; it lacks {capability!r}"
        raise LayoutInvariantError(message)
    try:
        installed = _installed_discord_py()
        applicable = _version_range(profile.version_expression)
    except (PackageNotFoundError, InvalidVersion, InvalidSpecifier) as error:
        message = f"cannot verify discord.py for adapter profile {profile.name!r}: {error}"
        raise LayoutInvariantError(message) from error
    if installed not in applicable:
        message = (
            f"adapter profile {profile.name!r} applies to discord.py {profile.version_expression}, "
            f"but {installed} is installed; supply a custom profile verified for this version"
        )
        raise LayoutInvariantError(message)


def require_discord_py_target(
    target: AnyTarget, capability: AdapterCapability, operation: str
) -> AdapterProfile[DiscordPyAdapter]:
    """Extract and verify the discord.py profile bound to a target."""
    profile = target.adapter
    if profile is None or not issubclass(profile.family, DiscordPyAdapter):
        message = f"target {target.id!r} cannot {operation}; it is not bound to a discord.py adapter profile"
        raise LayoutInvariantError(message)
    # `issubclass` on `profile.family` proves the family but cannot narrow the profile's
    # own parameter -- there is no spelling for "this generic argument is that class".
    narrowed = cast(AdapterProfile[DiscordPyAdapter], profile)
    require_discord_py_capability(narrowed, capability, operation)
    return narrowed
