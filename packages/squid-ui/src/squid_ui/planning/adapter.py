"""Dependency-neutral profiles for libraries that realize protocol targets."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from squid_ui.extensions import ExtensionKind as ExtensionKind
from squid_ui.planning.resources import EMPTY_COST as EMPTY_COST
from squid_ui.planning.resources import ResourceCost as ResourceCost
from squid_ui.scene.model import JsonValue


@dataclass(frozen=True, slots=True)
class PreparedExtension[ResourceT]:
    """What one `ExtensionAdapter.prepare` call returns: the extension's cost, scene record, and frontend object.

    Parameterized by `ResourceT` so a renderer downcasting `resource` has a type to check against.
    """

    cost: ResourceCost
    """Charged against the page: at least one `Axis.COMPONENTS`, `Axis.DISPLAY_TEXT` at or above zero."""
    scene_payload: Mapping[str, JsonValue]
    """Recorded on the planned `scene.Extension`; must be JSON, since the scene is serialized."""
    resource: ResourceT
    """The frontend object the renderer draws; process-local and never serialized."""


class ExtensionAdapter[PayloadT, ResourceT](Protocol):
    """Turns one `Extension` node's payload into a native item a target can draw.

    An `AdapterProfile.extensions` entry, keyed by the `ExtensionKind` wire name it answers for.
    """

    def prepare(self, payload: PayloadT) -> PreparedExtension[ResourceT]:
        """Build and measure the native item once, during lowering.

        Called for each `Extension` node whose kind this adapter is registered for; a kind with no
        adapter lowers to its portable fallback instead. V2 lowering raises `LayoutInvariantError`
        when the returned cost charges no components or negative display text.
        """
        ...


def extension_capability(kind: str) -> str:
    """`extension.{kind}`, the capability string one extension kind contributes; every call site spells it here."""
    return f"extension.{kind}"


class AdapterCapability(StrEnum):
    """One behavior a library has been verified to provide.

    A namespace apart from `Capability`, which is what the *protocol* can draw. The two
    unite into `Target.capabilities` as plain strings, so the membership tests that read
    that union are unaffected; keeping the declarations typed apart is what stops an
    adapter behavior being written where a protocol capability is meant.
    """

    RENDER_V2 = "adapter.discord.render.components-v2"
    RENDER_CLASSIC = "adapter.discord.render.classic"
    RENDER_HTML = "adapter.html.render"
    RENDER_SLACK_HOME = "adapter.slack.render.home"
    RENDER_SLACK_MESSAGE = "adapter.slack.render.message"
    RENDER_SLACK_MODAL = "adapter.slack.render.modal"
    DISPATCH = "adapter.discord.dispatch"
    INTERACTION_DELIVERY = "adapter.discord.interaction-delivery"
    MODAL_FORMS = "adapter.discord.modal-forms"


@dataclass(frozen=True, slots=True)
class AdapterProfile[AdapterT]:
    """Verified behavior supplied by one library family and version range.

    The second axis of a `Target`; `name` is what `Target.triple` and the fingerprint record.
    Raises `ValueError` when `name` or `version_expression` is empty.
    """

    family: type[AdapterT]
    """The marker type (`DiscordPyAdapter`, `SlackSdkAdapter`, ...) documents narrow to."""
    name: str
    version_expression: str
    """A PEP 440 specifier set such as `">=2.7,<2.8"`; the frontend checks the installed library against it."""
    capabilities: frozenset[AdapterCapability] = frozenset()
    extensions: Mapping[str, ExtensionAdapter[Any, Any]] = field(default_factory=dict)
    """Extension adapters, by the wire name of the `ExtensionKind` each answers for.

    Heterogeneous, so the container is `Any`: each kind pairs its own payload with its own
    frontend object, and the pairing is checked at the `ExtensionKind` and the adapter's own
    signature instead.
    """

    def __post_init__(self) -> None:
        if not self.name:
            message = "adapter profile name cannot be empty"
            raise ValueError(message)
        if not self.version_expression:
            message = "adapter profile version expression cannot be empty"
            raise ValueError(message)
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "extensions", MappingProxyType(dict(sorted(self.extensions.items()))))

    @property
    def extension_capabilities(self) -> frozenset[str]:
        """`extension_capability(kind)` for every registered extension kind."""
        return frozenset(extension_capability(kind) for kind in self.extensions)

    def combine_capabilities(self, protocol: frozenset[str]) -> frozenset[str]:
        """The flat string set a target exposes: `protocol` plus this profile's behaviors and extensions."""
        return protocol | self.capabilities | self.extension_capabilities
