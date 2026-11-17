"""Prepare Discord native nodes while sharing portable traversal and work estimates."""

from dataclasses import replace
from typing import Any, cast

from squid_ui.content.text import resolve_text
from squid_ui.ext.discord.planning.native_extensions import ComposableExtension
from squid_ui.ext.discord.primitives import nodes as primitive
from squid_ui.overflow import Alt, Alts, Condense, Drop, Never, Paginate, Spill, Truncate
from squid_ui.planning.composition_input import InputPreparation
from squid_ui.planning.dialect import NativeInput
from squid_ui.planning.semantic_index import UnsupportedSemanticError
from squid_ui.target_types import Renderable


class DiscordInput(NativeInput):
    """Validate native nesting and overflow rules for one planning call."""

    native_types: frozenset[type] = frozenset()

    def __init__(self, context: InputPreparation) -> None:
        self.context = context
        self.card_depth = 0

    def visit(self, node: Renderable[Any]) -> Renderable[Any] | None:
        context = self.context
        match node:
            case (
                primitive.Panel()
                | primitive.Section()
                | primitive.Thumbnail()
                | primitive.Sep()
                | primitive.Gallery()
                | primitive.MediaCollection()
                | primitive.File()
            ):
                if primitive.Panel not in self.native_types:
                    message = "native Components V2 layout on another target"
                    raise UnsupportedSemanticError(message)
                if isinstance(node, primitive.Panel):
                    return replace(
                        node, children=cast(tuple[primitive.Node, ...], tuple(map(context.visit, node.children)))
                    )
                if isinstance(node, primitive.Section):
                    if any(isinstance(text.overflow, Paginate) for text in node.texts):
                        message = "pagination cannot be nested in a Section; paginate its enclosing region"
                        raise UnsupportedSemanticError(message)
                    return replace(
                        node,
                        texts=cast(
                            tuple[primitive.Text | primitive.Heading | primitive.Footer, ...],
                            tuple(map(context.visit, node.texts)),
                        ),
                        accessory=cast(
                            primitive.Button
                            | primitive.RoutedButton
                            | primitive.LinkButton
                            | primitive.PremiumButton
                            | primitive.RawItem
                            | primitive.Thumbnail,
                            context.visit(node.accessory),
                        ),
                    )
                return node
            case primitive.Content():
                if primitive.Card not in self.native_types:
                    msg = "native classic message content on another target"
                    raise UnsupportedSemanticError(msg)
                context.visit(primitive.Text(node.content, overflow=node.overflow, priority=node.priority))
                return node
            case primitive.Card():
                if primitive.Card not in self.native_types:
                    msg = "native classic embed on another target"
                    raise UnsupportedSemanticError(msg)
                self.card_depth += 1
                try:
                    for child in node.children:
                        context.visit(child)
                    slots = [entry for field in node.fields for entry in (field.name, field.value)]
                    if node.title is not None:
                        slots.append(node.title)
                    if node.footer is not None:
                        slots.append(node.footer.text)
                    if node.author is not None:
                        slots.append(node.author.name)
                    for slot in slots:
                        context.visit(primitive.card_text(slot))
                finally:
                    self.card_depth -= 1
                return node
            case primitive.Budget() | primitive.Break():
                if primitive.Text not in self.native_types:
                    msg = "Discord native layout policy on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Budget) and primitive.Panel not in self.native_types:
                    msg = "Budget requires one shared character axis; use resource budgets on this target"
                    raise UnsupportedSemanticError(msg)
                for child in node.children:
                    context.visit(child)
                return node
            case primitive.Time() | primitive.ZonedTime():
                if primitive.Text not in self.native_types:
                    msg = "Discord native time on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.Extension():
                adapter = context.extensions.get(node.kind.name)
                if adapter is None or primitive.Panel not in self.native_types:
                    context.visit(node.fallback)
                elif not isinstance(adapter, ComposableExtension):
                    msg = f"extension {node.kind.name!r} needs a compose() contract for shared allocation"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.RawItem():
                if primitive.Panel not in self.native_types or node.composition is None:
                    msg = "RawItem requires a native Components V2 composition contract"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.Row() | primitive.ControlGroup():
                if primitive.Text not in self.native_types:
                    msg = "Discord native controls on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                for item in node.items:
                    if isinstance(item, primitive.RawItem):
                        context.visit(item)
                        continue
                    if not isinstance(
                        item, primitive.Button | primitive.RoutedButton | primitive.LinkButton | primitive.PremiumButton
                    ):
                        raise UnsupportedSemanticError(type(item).__name__)
                return node
            case (
                primitive.Button()
                | primitive.RoutedButton()
                | primitive.LinkButton()
                | primitive.PremiumButton()
                | primitive.SelectMenu()
                | primitive.RoutedSelect()
                | primitive.EntitySelect()
            ):
                if primitive.Text not in self.native_types:
                    msg = "Discord native controls on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                return node
            case primitive.Text() | primitive.Code() | primitive.Lines() | primitive.Heading() | primitive.Footer():
                if primitive.Text not in self.native_types:
                    msg = "Discord text primitives on a non-Discord target"
                    raise UnsupportedSemanticError(msg)
                overflow = node.overflow
                if self.card_depth and isinstance(overflow, Paginate):
                    msg = "embed text cannot contain pager controls; paginate the enclosing cards"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Lines) and isinstance(
                    overflow, Never | Truncate | Spill | Drop | Condense | Alts
                ):
                    steps = sum(len(item.fallbacks) if isinstance(item, Alt) else 0 for item in node.lines)
                    context.combinations = max(context.combinations, len(node.lines) + steps)
                    if isinstance(overflow, Alts):
                        context.text_choices += min(context.policy.text_ladder, len(overflow.ladder[-1]))
                        context.combinations = max(context.combinations, context.text_choices + len(overflow.ladder))
                    return node
                if isinstance(overflow, Never | Truncate | Alts | Drop | Spill | Condense) and not isinstance(
                    node, primitive.Lines
                ):
                    if isinstance(overflow, Truncate | Alts | Condense) or context.shortening_depth:
                        length = len(resolve_text(node.content, context.localization).plain())
                        if isinstance(overflow, Alts):
                            length = max(length, len(overflow.ladder[-1]))
                        context.text_choices += min(context.policy.text_ladder, length)
                        context.combinations = max(context.combinations, context.text_choices)
                    return node
                if not isinstance(overflow, Paginate):
                    msg = "primitive text overflow alternatives"
                    raise UnsupportedSemanticError(msg)
                if overflow.min_fill and primitive.Panel not in self.native_types:
                    msg = "primitive min_fill requires one shared character axis"
                    raise UnsupportedSemanticError(msg)
                if isinstance(node, primitive.Lines):
                    steps = sum(len(item.fallbacks) if isinstance(item, Alt) else 0 for item in node.lines)
                    context.combinations = max(context.combinations, len(node.lines) + steps)
                    if overflow.per is not None:
                        context.combinations *= max(1, (len(node.lines) + overflow.per - 1) // overflow.per)
                    return node
                content = resolve_text(node.content, context.localization).plain()
                count = len(content.split(overflow.boundary)) if overflow.boundary else 1
                context.combinations = max(context.combinations, count * 16)
                if overflow.per is not None:
                    context.combinations *= max(1, (count + overflow.per - 1) // overflow.per)
                return node
            case _:
                return None


class ClassicInput(DiscordInput):
    native_types = frozenset({primitive.Text, primitive.Card})


class V2Input(DiscordInput):
    native_types = frozenset({primitive.Text, primitive.Panel})
