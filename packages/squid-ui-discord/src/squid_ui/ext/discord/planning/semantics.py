"""Expose native Discord facts through the portable engine's extension contracts."""

from dataclasses import replace
from typing import Any, cast

from squid_ui.authoring.presentations import Alternatives
from squid_ui.content.text import Text
from squid_ui.ext.discord.planning.native import index_native, variant_alternatives
from squid_ui.ext.discord.planning.native_offers import native_offers
from squid_ui.ext.discord.planning.navigation import navigation_candidate
from squid_ui.ext.discord.planning.policy import DiscordOfferScope
from squid_ui.ext.discord.primitives import nodes as p
from squid_ui.native import AllocationComparison, NativeBudget, NativeTraits, NativeValue
from squid_ui.overflow import Alt, Alts, Paginate
from squid_ui.planning.offers.policy import Offers, OfferScope
from squid_ui.planning.semantic_index import SemanticIndexer


class DiscordSemantics:
    def traits(self, value: NativeValue) -> NativeTraits:
        key = getattr(value, "key", None)
        pagination = (
            value.overflow
            if isinstance(value, p.Text | p.Heading | p.Footer | p.Code | p.Lines)
            and isinstance(value.overflow, Paginate)
            else None
        )
        text = (
            value.content
            if isinstance(value, p.Text | p.Heading | p.Footer | p.Code)
            else (
                Text.join(value.join, (line.primary if isinstance(line, Alt) else line for line in value.lines))
                if isinstance(value, p.Lines) and isinstance(value.overflow, Alts)
                else None
            )
        )
        interaction: dict[str, object] | None = None
        match value:
            case p.Button():
                interaction = {
                    name: getattr(value, name) for name in ("on_click", "disabled", "mode", "guard", "busy", "record")
                }
                if isinstance(value, p.FormButton) and value.form is not None:
                    interaction["form"] = value.form
            case p.RoutedButton():
                interaction = {"route_id": value.route_id, "disabled": value.disabled}
            case p.LinkButton():
                interaction = {"url": value.url, "disabled": value.disabled}
            case p.PremiumButton():
                interaction = {"sku_id": value.sku_id}
            case p.SelectMenu() | p.RoutedSelect():
                interaction = {
                    "options": value.options,
                    "minimum": value.min_values,
                    "maximum": value.max_values,
                    "disabled": value.disabled,
                }
                if isinstance(value, p.SelectMenu):
                    interaction.update(on_select=value.on_select, mode=value.mode, routes=value.routes)
                else:
                    interaction["route_id"] = value.route_id
            case p.EntitySelect():
                interaction = {
                    name: getattr(value, name)
                    for name in ("entity_type", "on_select", "default_values", "conversation_types", "disabled", "mode")
                }
                interaction.update(minimum=value.min_values, maximum=value.max_values)
        boundary = None
        match value:
            case p.Row() | p.Content():
                boundary = ()
            case p.Section():
                boundary = (len(value.texts),)
            case p.Gallery():
                boundary = (len(value.items),)
            case p.Panel():
                boundary = (value.accent, value.spoiler)
            case p.Card():
                boundary = (
                    value.url,
                    value.accent,
                    value.image,
                    value.thumbnail,
                    value.timestamp,
                    tuple(entry.inline for entry in value.fields),
                    value.footer.icon_url if value.footer else None,
                    (value.author.url, value.author.icon_url) if value.author else None,
                )
            case p.Budget():
                boundary = (value.minimum, value.preferred, value.stretch)
            case p.Break():
                boundary = ()
        if pagination is not None:
            boundary = (pagination,)
        return NativeTraits(
            key=key or (pagination.key if pagination is not None else None),
            text=text,
            pagination=pagination,
            budget=NativeBudget(value.minimum, value.preferred, value.stretch, value.best_effort)
            if isinstance(value, p.Budget)
            else None,
            interaction=interaction,
            boundary=boundary,
            panel=isinstance(value, p.Panel | p.Card),
            transparent=isinstance(value, p.Budget | p.Break),
            unbreakable=isinstance(value, p.Break) and value.unbreakable,
            keep_with_next=isinstance(value, p.Break) and value.keep_with_next,
            content_axis=isinstance(value, p.Content),
            live=isinstance(value, p.Extension | p.RawItem),
            fallback=value.fallback if isinstance(value, p.Extension) else None,
        )

    def alternatives(self, value: NativeValue) -> Alternatives[Any, Any] | None:
        return variant_alternatives(value) if isinstance(value, p.Variants) else None

    def index(self, value: NativeValue, index: SemanticIndexer[Any, Any], path: tuple[str, ...], parent: Any) -> None:
        index_native(value, index, path, parent)

    def offers[R, C](self, value: NativeValue, scope: OfferScope[R, C]) -> Offers[R, C]:
        return native_offers(value, DiscordOfferScope.from_scope(scope))

    def navigation_candidate(self, value, occurrence, context, *, disabled_controls=True):
        if not disabled_controls and isinstance(value, p.Row):
            value = replace(value, items=tuple(item for item in value.items if not getattr(item, "disabled", False)))
        return navigation_candidate(value, occurrence, context)

    def namespace(self, value: NativeValue, prefix: str) -> NativeValue:
        def item(node):
            return replace(node, key=f"{prefix}.{node.key}") if isinstance(node, p.Button) else node

        match value:
            case p.Text() | p.Heading() | p.Footer() | p.Code() | p.Lines():
                return (
                    replace(value, overflow=replace(value.overflow, key=f"{prefix}.{value.overflow.key}"))
                    if isinstance(value.overflow, Paginate) and value.overflow.key is not None
                    else value
                )
            case p.Row() | p.ControlGroup():
                return replace(value, items=tuple(item(node) for node in value.items))
            case p.Section():
                return replace(
                    value,
                    texts=cast(
                        tuple[p.Text | p.Heading | p.Footer, ...],
                        tuple(self.namespace(text, prefix) for text in value.texts),
                    ),
                    accessory=item(value.accessory),
                )
            case p.SelectMenu():
                return replace(value, key=f"{prefix}.{value.key}")
            case _:
                return value

    def with_pagination(self, value: NativeValue, pagination: Paginate) -> NativeValue:
        assert isinstance(value, p.Text | p.Heading | p.Footer | p.Code | p.Lines)
        return replace(value, overflow=pagination)

    def same_allocation(self, left: NativeValue, right: NativeValue, comparison: AllocationComparison) -> bool:
        if type(left) is not type(right):
            return False
        match left, right:
            case ((p.Panel() | p.Budget() | p.Break()), (p.Panel() | p.Budget() | p.Break())):
                return replace(left, children=()) == replace(right, children=()) and comparison.same_children(
                    left.children, right.children
                )
            case p.Section(), p.Section():
                return comparison.same_children(left.texts, right.texts) and comparison.same_node(
                    left.accessory, right.accessory
                )
            case (
                (p.Lines() | p.Content() | p.Text() | p.Heading() | p.Footer() | p.Code()),
                (p.Lines() | p.Content() | p.Text() | p.Heading() | p.Footer() | p.Code()),
            ):
                a, b = left.overflow, right.overflow
                if isinstance(a, Paginate) and isinstance(b, Paginate):

                    def page(pager: Paginate) -> Paginate:
                        # An application-owned page is a render-time value, not structure.
                        return replace(pager, page=0) if pager.on_change is not None else pager

                    return replace(left, overflow=page(a)) == replace(right, overflow=page(b))
                return left == right
            case p.RawItem() | p.Extension() | p.Variants(), _:
                return False
            case _:
                return left == right


def install() -> None:
    p.DiscordValue.semantics = DiscordSemantics()
