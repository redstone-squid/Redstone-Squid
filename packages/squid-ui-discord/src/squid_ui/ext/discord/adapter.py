"""The verified discord.py adapter profile and boundary checks."""

from collections.abc import Callable, Mapping
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

import discord
from squid_ui import scene
from squid_ui.content.text import Localization
from squid_ui.errors import DrawInvariantError, LayoutInvariantError
from squid_ui.ext.discord.form_planning import DiscordFormPlan, plan_form
from squid_ui.ext.discord.inspection import _walk, audit_items, cost
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.target_types import DiscordPy27Adapter, DiscordPyAdapter
from squid_ui.extensions import ExtensionKind
from squid_ui.interactions.forms import FormSpec
from squid_ui.planning.adapter import (
    AdapterCapability,
    AdapterProfile,
    ExtensionAdapter,
    PreparedExtension,
)
from squid_ui.planning.composition import AtomicContract, PlanningContext, ResourceIdentity, SharedUse
from squid_ui.planning.native_extensions import NativeComposition, NativePlacement
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.planning.target import AnyTarget
from squid_ui.primitives.nodes import RawItem

type ItemFactory = Callable[[], discord.ui.Item[Any]]

DISCORD_ITEM = ExtensionKind[ItemFactory, discord.ui.Item[Any]]("discord.item")
"""A zero-argument factory for a measured native item with a portable fallback."""


class _DiscordItemExtension:
    def prepare(self, payload: ItemFactory) -> PreparedExtension[discord.ui.Item[Any]]:
        """Build and measure one native item during target planning."""
        # Untyped callers can bypass the extension payload annotation.
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

    def compose(self, payload: ItemFactory, *, context: PlanningContext) -> RawItem:
        prepared = self.prepare(payload)
        item = prepared.resource
        context.work.consume(1 + prepared.cost.get(Axis.COMPONENTS))
        placements: frozenset[NativePlacement] = (
            frozenset({"root"})
            if isinstance(item, discord.ui.Container)
            else frozenset({"row", "accessory"})
            if isinstance(item, discord.ui.Button)
            else frozenset({"accessory"})
            if isinstance(item, discord.ui.Thumbnail)
            else frozenset({"root", "panel"})
        )
        text = []
        assets = {}
        declared = {asset.name: asset for asset in context.assets}
        for child, path in _walk((item,)):
            if isinstance(child, discord.ui.Container) and len(path) > 1:
                message = "native Components V2 containers cannot nest"
                raise LayoutInvariantError(message)
            if isinstance(child, discord.ui.TextDisplay):
                text.append(child.content)
            urls = (
                (child.media.url,)
                if isinstance(child, discord.ui.File | discord.ui.Thumbnail)
                else tuple(entry.media.url for entry in child.items)
                if isinstance(child, discord.ui.MediaGallery)
                else ()
            )
            for url in urls:
                if url.startswith("attachment://"):
                    name = url.removeprefix("attachment://")
                    if name not in declared:
                        message = f"native attachment {name!r} must be declared in the document"
                        raise LayoutInvariantError(message)
                    asset = declared[name]
                    assets[asset.key] = asset
        uses = tuple(
            SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1}))
            for asset in assets.values()
        )
        return RawItem(
            lambda: item,
            text_cost=prepared.cost.get(Axis.DISPLAY_TEXT),
            component_cost=prepared.cost.get(Axis.COMPONENTS),
            kind=DISCORD_ITEM.name,
            version=1,
            payload=prepared.scene_payload,
            composition=NativeComposition(
                AtomicContract(prepared.cost, {use.identity: use.cost for use in uses}),
                _measure_native_item,
                placements,
                tuple(text),
                uses,
            ),
        )


def _measure_native_item(node: scene.Extension, resource: object, context: PlanningContext) -> ResourceCost:
    if not isinstance(resource, discord.ui.Item) or node.payload.get("native_kind") != type(resource).__name__:
        message = "native item disagrees with its declared extension payload"
        raise DrawInvariantError(message)
    limits = context.limits if isinstance(context.limits, V2Limits) else V2Limits()
    audit_items((resource,), limits=limits).raise_if_invalid()
    measured = cost(resource)
    context.work.consume(1 + measured.get(Axis.COMPONENTS))
    return measured


DISCORD_PY_BEHAVIOR_CAPABILITIES = frozenset(AdapterCapability)


def _plan_form(spec: FormSpec, *, target: AnyTarget, localization: Localization, strict: bool) -> DiscordFormPlan:
    require_discord_py_target(target, AdapterCapability.MODAL_FORMS, "plan a modal form")
    return plan_form(spec, localization=localization, limits=target.limits.components, strict=strict)


def escape_text(value: str) -> str:
    """Use discord.py for Markdown escaping; channel references also stay literal."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value), ignore_links=False).replace(
        "<#", "<\u200b#"
    )


DISCORD_PY_27_ADAPTER: AdapterProfile[DiscordPyAdapter] = AdapterProfile(
    DiscordPy27Adapter,
    "discord.py",
    ">=2.7,<2.8",
    DISCORD_PY_BEHAVIOR_CAPABILITIES,
    {DISCORD_ITEM.name: _DiscordItemExtension()},
    frontend="discord",
    form_planner=_plan_form,
    escape_text=escape_text,
)
"""Verified discord.py 2.7 profile, annotated at the family accepted by renderer defaults.

The wider annotation accommodates BasedPyright's invariant inference for the frozen
profile. The runtime family remains DiscordPy27Adapter.
"""


@cache
def _installed_discord_py() -> Version:
    """Return the installed discord.py version once per process."""
    return Version(version("discord.py"))


@cache
def _version_range(expression: str) -> SpecifierSet:
    """Parse and cache one adapter version constraint."""
    return SpecifierSet(expression)


def discord_py_adapter_profile(
    name: str,
    version_expression: str,
    *,
    capabilities: frozenset[AdapterCapability] = DISCORD_PY_BEHAVIOR_CAPABILITIES,
    extensions: Mapping[str, ExtensionAdapter[Any, Any]] | None = None,
) -> AdapterProfile[DiscordPyAdapter]:
    """Declare the SDK versions and capabilities verified by the application.

    This factory records the claim without testing SDK behavior. Adapter boundaries
    check the installed version and required capabilities against this declaration.
    """
    return AdapterProfile(
        DiscordPyAdapter,
        name,
        version_expression,
        capabilities,
        {} if extensions is None else extensions,
        frontend="discord",
        form_planner=_plan_form,
        escape_text=escape_text,
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
    if (
        # Dead to the checker, because `Target.adapter` is not optional. Kept because `AnyTarget`
        # is fully erased and one can be built by untyped code, where naming the missing profile
        # beats letting `issubclass` raise TypeError on None.
        profile is None  # pyright: ignore[reportUnnecessaryComparison]
        or not issubclass(profile.family, DiscordPyAdapter)
    ):
        message = f"target {target.id!r} cannot {operation}; it is not bound to a discord.py adapter profile"
        raise LayoutInvariantError(message)
    # `issubclass` on `profile.family` proves the family but cannot narrow the profile's
    # own parameter -- there is no spelling for "this generic argument is that class".
    narrowed = cast(AdapterProfile[DiscordPyAdapter], profile)
    require_discord_py_capability(narrowed, capability, operation)
    return narrowed
