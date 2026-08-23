"""The seam between what every target shares and what one target's shape decides.

Almost all of planning is target-neutral. Semantic adaptation, resource allocation, the
variant search, action bindings, caching, degradation accounting, and session state work
the same whichever message a document ends up in. Four things do not, and a
:class:`TargetDialect` is exactly those four and nothing else:

1. normalizing lowered primitives into the target's own shape;
2. validating structure only that target can judge;
3. paginating a document losslessly across that target's message-wide budgets;
4. building the exact scene body a renderer will draw.

The list is short on purpose. Anything else that wants to branch on the target is a shared
operation that has not been extracted yet, and adding a fifth method is the signal to go
extract it instead.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from squid_layouts.actions import ActionBinding
from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import FormBinding
from squid_layouts.planning.cursors import CursorCoordinator
from squid_layouts.planning.limits import DiscordLimits
from squid_layouts.planning.measure import MeasuredLayout, Realized
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.target import TargetProfile
from squid_layouts.primitives.nodes import (
    Button,
    FormButton,
    LinkButton,
    Node,
    RawItem,
    RoutedButton,
    SelectMenu,
    Thumbnail,
)
from squid_layouts.scene.model import (
    SceneBody,
    SceneButton,
    SceneExtension,
    SceneLink,
    SceneNode,
    SceneRoutedButton,
    SceneThumbnail,
)


@dataclass(slots=True)
class SceneBindings:
    """Callbacks, forms, and prepared resources collected while a scene is built.

    Shared by every dialect. What a control *looks* like is target shape; what it *does* is
    not, and an action key means the same thing to a mount whichever message drew it.
    """

    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    form_bindings: dict[str, FormBinding] = field(default_factory=dict)
    resources: dict[str, object] = field(default_factory=dict)

    def action(self, node: Button | SelectMenu) -> str:
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
        # Only buttons carry admission, busy feedback and recording; a select's guards and
        # histories, if any, live on the route bindings its grouped actions were lowered into.
        guard = node.guard if isinstance(node, Button) else None
        feedback = node.feedback if isinstance(node, Button) else None
        label = node.label if isinstance(node, Button) else ""
        record = node.record if isinstance(node, Button) else None
        for route_key, binding in routes.items():
            if route_key in self.bindings:
                message = f"duplicate action key {route_key!r}"
                raise LayoutInvariantError(message)
            self.bindings[route_key] = binding
        self.bindings[key] = ActionBinding(
            key=key,
            handler=handler,
            policy=node.policy,
            routes=routes,
            guard=guard,
            feedback=feedback,
            label=label,
            record=record,
        )
        return key

    def control(self, node: Thumbnail | LinkButton | Button | RoutedButton | RawItem, path: str) -> SceneNode:
        """Convert one leaf every target draws the same way."""
        match node:
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case LinkButton(label=label, url=url):
                return SceneLink(label, url)
            case RoutedButton(label=label, route_id=route_id):
                # No binding: the router owns dispatch, so the scene is complete without one.
                return SceneRoutedButton(
                    label=label,
                    route_id=route_id,
                    style=node.style,
                    emoji=node.emoji,
                    disabled=node.disabled,
                )
            case Button():
                return SceneButton(
                    label=node.label,
                    action=self.action(node),
                    style=node.style,
                    emoji=node.emoji,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case RawItem(factory=factory, kind=kind, version=version, payload=payload):
                resource = f"native:{path}"
                self.resources[resource] = factory()
                return SceneExtension(kind, version, {**payload, "resource": resource})


class TargetDialect(Protocol):
    """One target's shape, isolated from everything the targets share."""

    def normalize(self, nodes: Sequence[Node], target: TargetProfile, limits: DiscordLimits) -> tuple[Node, ...]:
        """Rewrite semantically lowered nodes into this target's own primitive shape."""
        ...

    def validate(self, nodes: Sequence[Node], limits: DiscordLimits) -> None:
        """Reject structure this target cannot draw. Raises `LayoutInvariantError`."""
        ...

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[str, int],
        limits: DiscordLimits,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        """Split an over-budget document into the fewest lossless pages this target allows."""
        ...

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> SceneBody:
        """Build the exact scene body a renderer for this target will draw."""
        ...
