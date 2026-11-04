"""Normalize, validate, paginate, and build scenes for Discord message modes.

`DiscordDialect` supplies the mode-specific operations used by the shared Discord
planner. `TargetDialect` is the broader contract used by all target families.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding
from squid_ui.planning.cursors import CursorCoordinator
from squid_ui.planning.layout_measurement.model import Realized
from squid_ui.planning.layout_measurement.solver import MeasuredLayout
from squid_ui.planning.limits import Axis, MessageLimits
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.resolved import emoji as resolved_emoji
from squid_ui.planning.resolved import optional_text as resolved_optional_text
from squid_ui.primitives.nodes import (
    Button,
    EntitySelect,
    FormButton,
    GalleryItem,
    LinkButton,
    Node,
    PremiumButton,
    RawItem,
    RoutedButton,
    SelectMenu,
    Thumbnail,
)

if TYPE_CHECKING:
    from squid_ui.planning.target import Target


def require_gallery_item(value: str | GalleryItem) -> GalleryItem:
    """Return a normalized gallery item or fail at the compiler invariant boundary."""
    if isinstance(value, str):
        message = "Gallery left a shorthand URL unnormalized"
        raise LayoutInvariantError(message)
    return value


@dataclass(slots=True)
class SceneBindings:
    """Callbacks, forms, and live resources collected while building one scene."""

    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    form_bindings: dict[str, FormBinding] = field(default_factory=dict)
    resources: dict[str, object] = field(default_factory=dict)

    def action(self, node: Button | SelectMenu | EntitySelect) -> str:
        key = node.key
        if isinstance(node, FormButton) and node.form is not None:
            # Recorded beside the binding, not in place of it: the button presents the form
            # and the binding submits it, and both answer to the same key.
            self.form_bindings[key] = node.form
        if key in self.bindings:
            message = f"duplicate action key {key!r}"
            raise LayoutInvariantError(message)
        handler = node.on_click if isinstance(node, Button) else node.on_select
        routes = node.routes if isinstance(node, SelectMenu) else {}
        # Button guards, busy state, and history belong to the button binding.
        # Grouped selects attach guards and history to their individual routes.
        guard = node.guard if isinstance(node, Button) else None
        busy = node.busy if isinstance(node, Button) else None
        label = resolved_optional_text(node.label) if isinstance(node, Button) else ""
        record = node.record if isinstance(node, Button) else None
        for route_key, binding in routes.items():
            if route_key in self.bindings:
                message = f"duplicate action key {route_key!r}"
                raise LayoutInvariantError(message)
            self.bindings[route_key] = binding
        self.bindings[key] = ActionBinding(
            key=key,
            handler=handler,
            mode=node.mode,
            routes=routes,
            guard=guard,
            busy=busy,
            label=label or "",
            record=record,
        )
        return key

    def control(
        self, node: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem, path: str
    ) -> scene.Node:
        """Convert one leaf every target draws the same way."""
        match node:
            case Thumbnail(url=url, description=description, spoiler=spoiler):
                return scene.Thumbnail(url, resolved_optional_text(description), spoiler)
            case LinkButton(label=label, url=url, emoji=emoji, disabled=disabled):
                return scene.Link(resolved_optional_text(label), url, resolved_emoji(emoji), disabled)
            case PremiumButton(sku_id=sku_id):
                return scene.PremiumButton(sku_id)
            case RoutedButton(label=label, route_id=route_id):
                # No binding: the router owns dispatch, so the scene is complete without one.
                return scene.RoutedButton(
                    label=resolved_optional_text(label),
                    route_id=route_id,
                    style=node.style,
                    emoji=resolved_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case Button():
                return scene.Button(
                    label=resolved_optional_text(node.label),
                    action=self.action(node),
                    style=node.style,
                    emoji=resolved_emoji(node.emoji),
                    disabled=node.disabled,
                    mode=node.mode,
                )
            case RawItem(factory=factory, kind=kind, version=version, payload=payload):
                resource = f"native:{path}"
                self.resources[resource] = factory()
                encoded = cast(Mapping[str, scene.JsonValue], {**payload, "resource": resource})
                return scene.Extension(kind, version, encoded)


class DiscordDialect[LimitsT: MessageLimits, BodyT: scene.Body, RenderTargetT](Protocol):
    """Operations and limits for one Discord message mode."""

    id: str
    """This protocol's stable name, recorded in every scene planned against it."""
    version: int
    capabilities: frozenset[Capability]
    """What the *protocol* can draw. Never an adapter behavior or an extension string."""
    render_target: type[RenderTargetT]
    """The marker type that decides which nodes a document for this dialect may hold."""
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool
    """Whether this message mode supports native extension resources."""

    def normalize(self, nodes: Sequence[Node], target: Target[LimitsT, BodyT, RenderTargetT, Any]) -> tuple[Node, ...]:
        """Rewrite semantically lowered nodes into this target's own primitive shape."""
        ...

    def validate(self, nodes: Sequence[Node], limits: LimitsT) -> None:
        """Reject structure this target cannot draw. Raises `LayoutInvariantError`."""
        ...

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[Axis, int],
        limits: LimitsT,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        """Pack an over-budget document into pages and return the selected page and total count."""
        ...

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> BodyT:
        """Build the exact scene body a renderer for this target will draw."""
        ...
