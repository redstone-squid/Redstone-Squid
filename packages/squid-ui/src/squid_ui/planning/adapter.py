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
    """An extension's measured cost, portable scene payload, and live renderer resource.

    The resource remains process-local; only the scene payload is serialized.
    The caller controls how often the adapter prepares it.
    """

    cost: ResourceCost
    scene_payload: Mapping[str, JsonValue]
    resource: ResourceT


class ExtensionAdapter[PayloadT, ResourceT](Protocol):
    """Prepare a logical extension payload for target planning and drawing."""

    def prepare(self, payload: PayloadT) -> PreparedExtension[ResourceT]: ...


def extension_capability(kind: str) -> str:
    """Return the shared capability name for an extension kind."""
    return f"extension.{kind}"


class AdapterCapability(StrEnum):
    """Behavior supported by a renderer library.

    Protocol capabilities describe what the target format can express. Adapter
    capabilities describe the library behavior available to realize that format;
    `Target.capabilities` combines both sets as strings.
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
    """Verified behavior supplied by one library family and version range."""

    frontend: str | None = field(default=None, kw_only=True)
    """Dispatch identity used to select frontend-specific callback implementations."""

    family: type[AdapterT]
    name: str
    version_expression: str
    capabilities: frozenset[AdapterCapability] = frozenset()
    extensions: Mapping[str, ExtensionAdapter[Any, Any]] = field(default_factory=dict)
    """Extension adapters keyed by wire name. Each kind has its own payload and resource types."""

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
        """Capabilities contributed by this profile's target extensions."""
        return frozenset(extension_capability(kind) for kind in self.extensions)

    def combine_capabilities(self, protocol: frozenset[str]) -> frozenset[str]:
        """Combine protocol, behavior, and extension capabilities."""
        return protocol | self.capabilities | self.extension_capabilities
