"""Dependency-neutral profiles for libraries that realize protocol targets."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from squid_layouts.planning.target import ExtensionAdapter


ADAPTER_RENDER_V2 = "adapter.discord.render.components-v2"
ADAPTER_RENDER_CLASSIC = "adapter.discord.render.classic"
ADAPTER_DISPATCH = "adapter.discord.dispatch"
ADAPTER_INTERACTION_DELIVERY = "adapter.discord.interaction-delivery"
ADAPTER_MODAL_FORMS = "adapter.discord.modal-forms"


@dataclass(frozen=True, slots=True)
class AdapterProfile[AdapterT]:
    """Verified behavior supplied by one library family and version range."""

    family: type[AdapterT]
    name: str
    version_expression: str
    capabilities: frozenset[str] = frozenset()
    extensions: Mapping[str, ExtensionAdapter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("adapter profile name cannot be empty")
        if not self.version_expression:
            raise ValueError("adapter profile version expression cannot be empty")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "extensions", MappingProxyType(dict(sorted(self.extensions.items()))))

    @property
    def extension_capabilities(self) -> frozenset[str]:
        """Capabilities contributed by this profile's target extensions."""
        return frozenset(f"extension.{kind}" for kind in self.extensions)

    def combine_capabilities(self, protocol: frozenset[str]) -> frozenset[str]:
        """Combine protocol, behavior, and extension capabilities."""
        return protocol | self.capabilities | self.extension_capabilities
