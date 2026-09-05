"""The Discord-specific dialect seam used by the layout solver.

Almost all of planning is target-neutral. Semantic adaptation, resource allocation, the
variant search, action bindings, caching, degradation accounting, and session state work
the same whichever message a document ends up in. Four things do not, and a
`DiscordDialect` is exactly those four and nothing else:

1. normalizing lowered primitives into the target's own shape;
2. validating structure only that target can judge;
3. paginating a document losslessly across that target's message-wide budgets;
4. building the exact scene body a renderer will draw.

Anything else that wants to branch on the target is a shared operation that has not been
extracted yet; a fifth method is the signal to extract it instead. The data members are the
protocol's identity — what it is called, what it can draw, what a legal message holds — and a
target is the product of one dialect and one adapter.
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


@dataclass(slots=True)
class SceneBindings:
    """Callbacks, forms, and prepared resources collected while a scene is built.

    Shared by every dialect. What a control *looks* like is target shape; what it *does* is
    not, and an action key means the same thing to a mount whichever message drew it.
    """

    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    form_bindings: dict[str, FormBinding] = field(default_factory=dict)
    """Forms a `FormButton` presents, beside the binding that submits them, under the same key."""
    resources: dict[str, object] = field(default_factory=dict)
    """Native items by `native:{path}`, the reference the scene's `Extension` payload carries."""

    def action(self, node: Button | SelectMenu | EntitySelect) -> str:
        """Bind the node's handler under its key and return that key.

        Raises `LayoutInvariantError` when the key, or one of a select's route keys, is already bound.
        """
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
        # Only buttons carry admission, busy busy and recording; a select's guards and
        # histories, if any, live on the route bindings its grouped actions were lowered into.
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
        """Convert one leaf every target draws the same way; a `RawItem` is stored in `resources` under `path`."""
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
    """One Discord protocol mode: what a legal message of it is, and how to build one.

    The first axis of a target. Bound to its own limits and body types, so each dialect's
    four methods can narrow to what it actually handles instead of declaring the union and
    narrowing anyway. The data members repeat `TargetDialect`'s, since a Discord dialect
    satisfies both.
    """

    id: str
    """This protocol's stable name, recorded in every scene planned against it."""
    version: int
    """The scene's `target_version` and part of the target fingerprint; a renderer refuses one it cannot draw."""
    capabilities: frozenset[Capability]
    """What the *protocol* can draw. Never an adapter behavior or an extension string."""
    render_target: type[RenderTargetT]
    """The marker type that decides which nodes a document for this dialect may hold."""
    body_type: type[BodyT]
    """The `scene.Body` subclass `body` builds."""
    default_limits: LimitsT
    """The limits a target built without an explicit table gets."""
    realizes_extensions: bool
    """Whether an `Extension` can lower to a native item here; false for classic, which always takes the fallback."""

    def normalize(self, nodes: Sequence[Node], target: Target[LimitsT, BodyT, RenderTargetT, Any]) -> tuple[Node, ...]:
        """Rewrite semantically lowered nodes into this target's own primitive shape.

        Runs once before validation: `ControlGroup`s become rows, `Variants` rungs the target
        cannot draw are dropped, extensions are prepared or replaced by their fallback. Raises
        `LayoutInvariantError` when a `Variants` ladder has no supported rung.
        """
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
        """Split an over-budget document into the fewest lossless pages this target allows.

        Returns the measured page the cursor under `key` selects, with its footer and `nav`
        controls, and the page count. Records the cursor with `broker`. Raises
        `UnsolvableLayoutError` when one node alone overspends `capacities`.
        """
        ...

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> BodyT:
        """Build the exact scene body a renderer for this target will draw.

        Every action key is bound into `bindings` on the way. Raises `LayoutInvariantError` for
        a realized node this dialect has no scene form for.
        """
        ...
