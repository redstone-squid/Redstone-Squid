"""The component rules both Discord dialects enforce, stated once.

Components V2 and classic messages disagree about structure — panels and sections against
embeds and content — but they draw the same buttons and selects under the same
`ComponentLimits`. Both validators had grown their own copy of those checks, so a cap
corrected in one dialect stayed wrong in the other.

A dialect's own `match` runs first and delegates here for whatever it does not claim. That
order is load-bearing: a structure one dialect rejects outright is rejected before the shared
rules can read it as an ordinary control.
"""

from collections.abc import Callable
from typing import NoReturn

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.limits import MessageLimits
from squid_ui.planning.resolved import optional_text as resolved_optional_text
from squid_ui.planning.resolved import text as resolved_text
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
    Boundary,
    Break,
    Budget,
    Button,
    EntitySelect,
    LinkButton,
    Node,
    PremiumButton,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Variants,
)

type Walk = Callable[[Node, str], None]
"""How a dialect re-enters its own validator for a child it owns."""


def fail(path: str, detail: str) -> NoReturn:
    """Reject a node, naming where in the document it sits."""
    message = f"{path}: {detail}"
    raise LayoutInvariantError(message)


def register_pager(node: Node, path: str, pager_keys: set[str]) -> None:
    """Claim this node's pager key, rejecting a missing or already-taken one.

    `Boundary` is rejected here too. It is a mount-expansion placeholder, and every walk that
    collects pager keys is also a walk that must not still be seeing one.
    """
    if isinstance(node, Boundary):
        fail(path, "Boundary must be expanded by a component mount before planning")
    overflow = getattr(node, "overflow", None)
    if not isinstance(overflow, Paginate):
        return
    key = overflow.key
    if key is None:
        fail(path, "Paginate requires an explicit key")
    if key in pager_keys:
        fail(path, f"duplicate pager key {key!r}")
    pager_keys.add(key)


def validate_component(node: Node, path: str, *, limits: MessageLimits, walk: Walk) -> None:
    """Check the controls and wrappers that mean the same thing in either dialect.

    Anything else is left alone: the dialect has already had its turn, so a node neither
    layer claims is one with no shared component rule to break.
    """
    match node:
        case Button(label=label) | RoutedButton(label=label):
            label = resolved_optional_text(label)
            if label is None and node.emoji is None:
                fail(path, "interactive button needs a label or emoji")
            if label is not None and len(label) > limits.components.button_label:
                fail(path, f"button label exceeds {limits.components.button_label}")
        case LinkButton(label=label, url=url):
            label = resolved_optional_text(label)
            if label is None and node.emoji is None:
                fail(path, "link button needs a label or emoji")
            if label is not None and len(label) > limits.components.button_label:
                fail(path, f"button label exceeds {limits.components.button_label}")
            if len(url) > limits.components.link_url:
                fail(path, f"link URL exceeds {limits.components.link_url}")
        case Row(items=items):
            if len(items) > limits.components.row_buttons:
                fail(path, f"row has {len(items)} controls; maximum is {limits.components.row_buttons}")
            for index, item in enumerate(items):
                if isinstance(item, Button | RoutedButton | LinkButton | PremiumButton):
                    walk(item, f"{path}.{index}")
        case SelectMenu(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum) | (
            RoutedSelect(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum)
        ):
            placeholder = resolved_optional_text(placeholder)
            if not options:
                fail(path, "select needs at least one option")
            if len(options) > limits.components.select_options:
                remedy = (
                    "split the routed picker into separate routes"
                    if isinstance(node, RoutedSelect)
                    else "use an option-paging semantic node"
                )
                fail(path, f"select has {len(options)} options; {remedy}")
            if placeholder is not None and len(placeholder) > limits.components.select_placeholder:
                fail(path, f"select placeholder exceeds {limits.components.select_placeholder}")
            if minimum < 0 or maximum < minimum or maximum > max(1, len(options)):
                fail(path, "select value bounds are invalid")
            for index, option in enumerate(options):
                label = resolved_text(option.label)
                description = resolved_optional_text(option.description)
                if len(label) > limits.components.option_label:
                    fail(f"{path}.option.{index}", f"label exceeds {limits.components.option_label}")
                if len(option.value) > limits.components.option_value:
                    fail(f"{path}.option.{index}", f"value exceeds {limits.components.option_value}")
                if description is not None and len(description) > limits.components.option_description:
                    fail(f"{path}.option.{index}", f"description exceeds {limits.components.option_description}")
        case EntitySelect(
            placeholder=placeholder,
            default_values=defaults,
            min_values=minimum,
            max_values=maximum,
        ):
            placeholder = resolved_optional_text(placeholder)
            if placeholder is not None and len(placeholder) > limits.components.select_placeholder:
                fail(path, f"select placeholder exceeds {limits.components.select_placeholder}")
            if minimum < 0 or maximum < minimum or maximum > limits.components.select_options:
                fail(path, "entity select value bounds are invalid")
            if len(defaults) > maximum:
                fail(path, "entity select has more defaults than max_values")
        case Budget(children=children) | Break(children=children):
            for index, child in enumerate(children):
                walk(child, f"{path}.{index}")
        case Variants(variants=variants):
            # Every rung is checked, not just the one the search will open on, so a document
            # is rejected for a bad rung it might never reach. That also means two rungs
            # cannot share a Paginate key — as under the previous Fold, whose primary and
            # fallback were both walked.
            for index, variant in enumerate(variants):
                for child_index, child in enumerate(variant.nodes):
                    walk(child, f"{path}.variant.{index}.{child_index}")
        case _:
            return
