"""Discord native structure, preservation rules, and shared planner integration."""

from dataclasses import dataclass
from datetime import UTC
from typing import Any, Literal, cast

from squid_i18n import tree
from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import Alternative, Alternatives, Fidelity, SemanticRef
from squid_ui.content.text import resolve_text, timestamp_text
from squid_ui.ext.discord.planning.policy import DiscordOfferScope
from squid_ui.ext.discord.primitives import nodes as primitive
from squid_ui.native import NativeValue
from squid_ui.overflow import Alt, Alts
from squid_ui.planning.composition import RealizationOffer
from squid_ui.planning.offers.policy import Offers
from squid_ui.planning.realizations import Realization, TimeRealization
from squid_ui.planning.semantic_index import SemanticIndexer, UnsupportedSemanticError
from squid_ui.target_types import Renderable


@dataclass(frozen=True, slots=True)
class CardSlot(primitive.DiscordValue):
    kind: Literal["title", "field_name", "field_value", "footer", "author"]
    text: primitive.Text


def variant_alternatives(value: primitive.Variants[Any]) -> Alternatives[Any, Any]:
    """Expose native ladders through checked coverage and interaction declarations."""
    choices: list[Alternative[Any, Any]] = []
    for index, variant in enumerate(value.variants):
        if variant.fidelity is primitive.Fidelity.LOSSY and variant.contract is None:
            message = "lossy native Variants require an explicit Variant.contract"
            raise UnsupportedSemanticError(message)
        choices.append(
            Alternative(
                sem.Group[Any, Any](variant.nodes, key="@content"),
                key=f"rung-{index}",
                fidelity=variant.contract or Fidelity(),
                preference=index
                + len(value.variants)
                * (
                    2
                    if variant.contract is not None and not variant.contract.lossless
                    else 1
                    if variant.fidelity is primitive.Fidelity.REFORMATTED
                    else 0
                ),
                correspondence=variant.correspondence,
                requires=variant.requires,
            )
        )
    return Alternatives(
        value.content or sem.Group[Any, Any](value.variants[0].nodes, key="@content"),
        tuple(choices),
        key=value.key or "@variants",
        priority=value.priority,
    )


def _timestamp_offers[RenderTargetT, ContextT](
    scope: DiscordOfferScope[RenderTargetT, ContextT],
    value: sem.Timestamp | sem.ZonedTimestamp | primitive.Time | primitive.ZonedTime,
) -> Offers[RenderTargetT, ContextT]:
    occurrence = scope.occurrence
    context = scope.context
    if isinstance(value, primitive.Time | primitive.ZonedTime) and not scope.policy.native_controls:
        return ()
    label = (
        resolve_text(value.label, context.localization).nodes
        if isinstance(value, sem.Timestamp | sem.ZonedTimestamp) and value.label is not None
        else ()
    )
    if isinstance(value, sem.Timestamp | primitive.Time):
        instant = value.instant
        style = sem.TimeStyle(value.style).value
        timestamp = timestamp_text(instant, style=style)
        time_nodes = resolve_text(timestamp, context.localization).nodes
        display = instant.isoformat()
        timezone = None
    else:
        instant = value.value.instant
        style = None
        display = value.value.isoformat()
        time_nodes = (tree.literal(display),)
        timezone = value.value.timezone
    prefix = (
        (tree.Node("strong", children=(*label, tree.literal(":")), tag="strong", markup="**"), tree.literal(" "))
        if label
        else ()
    )
    if isinstance(value, primitive.Time | primitive.ZonedTime) and value.prefix is not None:
        prefix = resolve_text(value.prefix, context.localization).nodes
        label = prefix
    return (
        RealizationOffer[RenderTargetT, ContextT, Realization](
            occurrence,
            "time",
            TimeRealization(
                (*prefix, *time_nodes),
                instant=instant.astimezone(UTC).isoformat(),
                display=display,
                style=style,
                timezone=timezone,
                label=label,
                prefix=prefix,
                native_prefix=value.prefix if isinstance(value, primitive.Time | primitive.ZonedTime) else None,
            ),
        ),
    )


def index_native(
    value: NativeValue, index: SemanticIndexer[Any, Any], path: tuple[str, ...], parent: SemanticRef | None
) -> None:
    add, visit = index.add, index.visit
    match value:
        case primitive.Extension():
            root = add(value, path, parent, atomic=False)
            visit(cast(Renderable[Any, Any], value.fallback), root, 0, name="@fallback")

        case primitive.Row() | primitive.ControlGroup():
            root = add(value, path, parent, atomic=False)
            for i, control in enumerate(value.items):
                visit(control, root, i)

        case primitive.Content():
            root = add(value, path, parent, atomic=False)
            add(
                primitive.Text(value.content, overflow=value.overflow, priority=value.priority),
                (*path, "@text"),
                root,
                atomic=True,
            )

        case primitive.Card():
            root = add(value, path, parent, atomic=False)
            description = add(
                sem.Group[Any, Any]((), key="@description"),
                (*path, "@description"),
                root,
                atomic=False,
            )
            for i, child in enumerate(value.children):
                visit(child, description, i)
            if value.title is not None:
                add(CardSlot("title", primitive.card_text(value.title)), (*path, "@title"), root, atomic=True)
            if value.footer is not None:
                add(
                    CardSlot("footer", primitive.card_text(value.footer.text)),
                    (*path, "@footer"),
                    root,
                    atomic=True,
                )
            if value.author is not None:
                add(
                    CardSlot("author", primitive.card_text(value.author.name)),
                    (*path, "@author"),
                    root,
                    atomic=True,
                )
            for i, entry in enumerate(value.fields):
                add(
                    CardSlot("field_name", primitive.card_text(entry.name)),
                    (*path, f"@field-{i}-name"),
                    root,
                    atomic=True,
                )
                add(
                    CardSlot("field_value", primitive.card_text(entry.value)),
                    (*path, f"@field-{i}-value"),
                    root,
                    atomic=True,
                )

        case primitive.Gallery() | primitive.MediaCollection():
            root = add(value, path, parent, atomic=False)
            for i, item in enumerate(value.items):
                visit(primitive.normalize_gallery_item(item), root, i)

        case primitive.Lines(overflow=Alts()):
            # A whole-collection replacement supplies no per-line correspondence.
            root = add(value, path, parent, atomic=True)

        case primitive.Lines():
            root = add(value, path, parent, atomic=False)
            for i, line in enumerate(value.lines):
                visit(
                    sem.ListItem(
                        value.keys[i] if value.keys else f"@{i}", line.primary if isinstance(line, Alt) else line
                    ),
                    root,
                    i,
                )

        case primitive.Section():
            root = add(value, path, parent, atomic=False)
            for i, child in enumerate(value.texts):
                visit(child, root, i)
            visit(value.accessory, root, len(value.texts), name="@accessory")

        case primitive.Budget() | primitive.Break():
            root = add(value, path, parent, atomic=False)
            for i, child in enumerate(value.children):
                # Native policy wrappers carry Discord nodes; their generic marker
                # cannot express that child-target restriction to the type checker.
                visit(cast(Renderable[Any, Any], child), root, i)

        case primitive.Panel():
            root = add(value, path, parent, atomic=False)
            for i, child in enumerate(value.children):
                visit(child, root, i)

        case (
            primitive.GalleryItem()
            | primitive.File()
            | primitive.RawItem()
            | primitive.Button()
            | primitive.RoutedButton()
            | primitive.LinkButton()
            | primitive.PremiumButton()
            | primitive.SelectMenu()
            | primitive.RoutedSelect()
            | primitive.EntitySelect()
            | primitive.Text()
            | primitive.Heading()
            | primitive.Footer()
            | primitive.Code()
            | primitive.Thumbnail()
            | primitive.Sep()
            | primitive.Time()
            | primitive.ZonedTime()
        ):
            add(value, path, parent, atomic=True)

        case _:
            raise UnsupportedSemanticError(type(value).__name__)
