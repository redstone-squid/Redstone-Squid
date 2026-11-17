"""Index authored semantic units and their source identities."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Alternative,
    Alternatives,
    AtomicNode,
    Budget,
    Fidelity,
    SemanticRef,
    TextPresentation,
)
from squid_ui.authoring.rosters import RosterEntry, RosterSlot
from squid_ui.content.text import TextLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.interaction_handlers import ForwardSelection
from squid_ui.planning.interaction_semantics import (
    DisclosureToggle,
    Interaction,
    ItemBack,
    ItemSummary,
    NavigationChoice,
)
from squid_ui.planning.layout_structure import LAYOUT_CONTAINERS, layout_parts
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import Alt, Alts, Paginate
from squid_ui.target_types import Renderable


class UnsupportedSemanticError(LayoutInvariantError):
    """The shared graph has no declared semantics for this vocabulary yet."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"semantic correspondence does not yet support {kind}")


@dataclass(frozen=True, slots=True)
class TableCell:
    row: str
    column: str
    content: TextLike


@dataclass(frozen=True, slots=True)
class FieldPart:
    part: Literal["label", "value"]
    content: TextLike


@dataclass(frozen=True, slots=True)
class CardSlot:
    """Text in one named classic embed slot, which has its own character limit."""

    kind: Literal["title", "field_name", "field_value", "footer", "author"]
    text: primitive.Text


@dataclass(frozen=True, slots=True)
class RosterSection:
    """One roster slot's group of members, kept so continued member lines still know their slot."""

    slot: RosterSlot | None
    count: int
    full: bool = False


@dataclass(frozen=True, slots=True)
class RosterLabel:
    section: RosterSection
    full: bool = False


@dataclass(frozen=True, slots=True)
class RosterMembers(sem.List):
    """Member lines use their enclosing region instead of introducing a local pager."""


@dataclass(frozen=True, slots=True)
class StructuralGroup:
    part: Literal["columns", "rows", "header", "footer"]


@dataclass(frozen=True, slots=True)
class ItemBranch[RenderTargetT, ContextT]:
    items: sem.Items[RenderTargetT, ContextT]
    item: sem.Item[RenderTargetT, ContextT]


type EmbeddedValue[ContextT] = (
    sem.Column
    | sem.TableRow
    | sem.Field
    | sem.ListItem
    | sem.MediaItem
    | RosterMembers
    | primitive.GalleryItem
    | sem.ControlGroup[ContextT]
    | Interaction[ContextT]
)
type SemanticValue[RenderTargetT, ContextT] = (
    sem.SemanticNode[RenderTargetT, ContextT]
    | sem.Adaptation[RenderTargetT, ContextT]
    | sem.FallbackContent[RenderTargetT, ContextT]
    | EmbeddedValue[ContextT]
    | Budget[RenderTargetT, ContextT]
    | Alternatives[RenderTargetT, ContextT]
    | AtomicNode[RenderTargetT, ContextT]
    | RosterSection
    | RosterLabel
    | CardSlot
    | primitive.Extension[Any, RenderTargetT]
    | primitive.RawItem
    | primitive.Card
    | primitive.Content
    | TableCell
    | FieldPart
    | TextPresentation
    | StructuralGroup
    | ItemBranch[RenderTargetT, ContextT]
    | primitive.Row[ContextT]
    | primitive.ControlGroup[ContextT]
    | primitive.Text
    | primitive.Heading
    | primitive.Footer
    | primitive.Code
    | primitive.Lines
    | primitive.Section[ContextT]
    | primitive.Budget[RenderTargetT, ContextT]
    | primitive.Break[RenderTargetT, ContextT]
    | primitive.Panel[ContextT]
    | primitive.Gallery
    | primitive.MediaCollection
    | primitive.File
    | primitive.Thumbnail
    | primitive.Sep
    | primitive.Time
    | primitive.ZonedTime
)


@dataclass(frozen=True, slots=True)
class SemanticUnit[RenderTargetT, ContextT]:
    reference: SemanticRef
    value: SemanticValue[RenderTargetT, ContextT]
    parent: SemanticRef | None
    atomic: bool


@dataclass(frozen=True, slots=True)
class SemanticGraph[RenderTargetT, ContextT]:
    units: Mapping[SemanticRef, SemanticUnit[RenderTargetT, ContextT]]
    children: Mapping[SemanticRef, tuple[SemanticRef, ...]]

    def resolve(self, reference: SemanticRef) -> SemanticRef:
        if reference in self.units:
            return reference
        matches = [key for key in self.units if key.keys[-len(reference.keys) :] == reference.keys]
        if len(matches) != 1:
            message = f"semantic reference {reference.keys!r} is {'ambiguous' if matches else 'unknown'}"
            raise LayoutInvariantError(message)
        return matches[0]

    def atoms(self, reference: SemanticRef) -> frozenset[SemanticRef]:
        root = self.resolve(reference)
        return frozenset(
            key for key, unit in self.units.items() if unit.atomic and key.keys[: len(root.keys)] == root.keys
        )

    @property
    def leaves(self) -> frozenset[SemanticRef]:
        return frozenset(key for key, unit in self.units.items() if unit.atomic)


def _key(value: object) -> str | None:
    match value:
        case (
            sem.Group()
            | sem.Stack()
            | sem.Block()
            | sem.Section()
            | sem.Paragraph()
            | sem.Table()
            | sem.Grid()
            | sem.Roster()
            | sem.List()
            | sem.ListItem()
            | sem.Column()
            | sem.TableRow()
            | sem.Field()
            | sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.ControlGroup()
            | sem.ActionControls()
            | sem.FormTrigger()
            | sem.Choices()
            | sem.Entities()
            | sem.Navigation()
            | sem.Details()
            | sem.Items()
            | sem.Toggle()
            | sem.RoutedChoices()
            | sem.Region()
            | sem.Paged()
            | sem.Download()
            | sem.Media()
            | sem.MediaItem()
            | TextPresentation()
            | Alternatives()
            | primitive.Variants()
            | AtomicNode()
            | primitive.Button()
            | primitive.SelectMenu()
            | primitive.EntitySelect()
        ):
            return value.key
        case (
            primitive.Lines(overflow=Paginate(key=key))
            | primitive.Text(overflow=Paginate(key=key))
            | primitive.Heading(overflow=Paginate(key=key))
            | primitive.Footer(overflow=Paginate(key=key))
            | primitive.Code(overflow=Paginate(key=key))
        ):
            return key
        case _:
            return None


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


def semantic_graph[RenderTargetT, ContextT](
    node: Renderable[RenderTargetT, ContextT],
    *,
    consume: Callable[[], None] | None = None,
) -> SemanticGraph[RenderTargetT, ContextT]:
    """Index supported structure without lowering it or invoking application callbacks.

    Keys are scoped by their containers. A short reference is accepted only when
    unambiguous. Positional identities do not promise stability under reordering.
    Unsupported nodes are rejected rather than guessed atomic from their shape.
    """
    units: dict[SemanticRef, SemanticUnit[RenderTargetT, ContextT]] = {}

    def add(
        value: SemanticValue[RenderTargetT, ContextT],
        path: tuple[str, ...],
        parent: SemanticRef | None,
        *,
        atomic: bool,
    ) -> SemanticRef:
        if consume is not None:
            consume()
        reference = SemanticRef(path)
        if reference in units:
            message = f"duplicate semantic identity {path!r}"
            raise LayoutInvariantError(message)
        units[reference] = SemanticUnit(reference, value, parent, atomic)
        return reference

    def visit(
        value: Renderable[Any, ContextT] | EmbeddedValue[ContextT],
        parent: SemanticRef | None,
        index: int,
        *,
        name: str | None = None,
    ) -> None:
        key = name or _key(value) or f"@{index}"
        path = (*(() if parent is None else parent.keys), key)
        if isinstance(value, primitive.Variants):
            value = variant_alternatives(value)
        match value:
            case Alternatives():
                root = add(value, path, parent, atomic=False)
                visit(value.content, root, 0)
            case primitive.Extension():
                root = add(value, path, parent, atomic=False)
                visit(cast(Renderable[RenderTargetT, ContextT], value.fallback), root, 0, name="@fallback")
            case sem.FallbackContent():
                root = add(value, path, parent, atomic=False)
                for i, branch in enumerate((value.primary, *value.alternates)):
                    visit(sem.Group((branch,), key=f"@fallback-{i}"), root, i)
            case (
                sem.Unbreakable()
                | sem.KeepWithNext()
                | sem.Budgeted()
                | sem.Paged()
                | sem.OptionalContent()
                | sem.Truncated()
                | sem.Spilled()
                | sem.BestEffort()
                | Budget()
            ):
                root = add(value, path, parent, atomic=False)
                visit(value.node, root, 0)
            case sem.Roster():
                root = add(value, path, parent, atomic=False)
                roster_groups: list[tuple[RosterSlot | None, tuple[RosterEntry, ...]]] = [
                    (group.slot, group.members) for group in value.placement.groups
                ]
                if value.show_waitlist and value.placement.waitlist:
                    roster_groups.append((None, value.placement.waitlist))
                for slot, members in roster_groups:
                    full = slot is not None and slot.capacity is not None and len(members) >= slot.capacity
                    section = RosterSection(slot, len(members), full and value.placement.rejects_overflow)
                    section_key = slot.key if slot is not None else "@waitlist"
                    group = add(section, (*path, section_key), root, atomic=False)
                    add(RosterLabel(section), (*group.keys, "@heading"), group, atomic=True)
                    if members:
                        visit(
                            RosterMembers(
                                tuple(sem.ListItem(member.actor_id, member.display) for member in members),
                                key="@members",
                            ),
                            group,
                            0,
                        )
                    if section.full:
                        add(RosterLabel(section, full=True), (*group.keys, "@full"), group, atomic=True)
                    if slot is not None:
                        available = not value.locked and not section.full
                        if value.on_join is not None:
                            visit(
                                sem.ActionControl(
                                    f"{value.key}.{slot.key}",
                                    slot.label,
                                    ForwardSelection(value.on_join, slot.key),
                                    available=available,
                                    tone=slot.tone,
                                ),
                                group,
                                1,
                            )
                        elif value.routes is not None:
                            visit(
                                sem.RoutedActionControl(
                                    f"{value.key}.{slot.key}",
                                    slot.label,
                                    value.routes[slot.key],
                                    available=available,
                                    tone=slot.tone,
                                ),
                                group,
                                1,
                            )
            case sem.Grid():
                root = add(value, path, parent, atomic=False)
                for i, cell in enumerate(value.cells):
                    visit(
                        sem.ActionControl(
                            cell.key,
                            cell.label,
                            ForwardSelection(value.on_pick, cell.key),
                            available=cell.available,
                            tone=cell.tone,
                        ),
                        root,
                        i,
                    )
            case sem.Table():
                root = add(value, path, parent, atomic=False)
                columns = add(StructuralGroup("columns"), (*path, "columns"), root, atomic=False)
                for i, column in enumerate(value.columns.columns):
                    visit(column, columns, i)
                rows = add(StructuralGroup("rows"), (*path, "rows"), root, atomic=False)
                for i, row in enumerate(value.rows):
                    row_key = row.key or f"@{i}"
                    row_ref = add(row, (*rows.keys, row_key), rows, atomic=False)
                    for j, (column, cell) in enumerate(zip(value.columns.columns, row.cells, strict=True)):
                        column_key = column.key or f"@{j}"
                        add(TableCell(row_key, column_key, cell), (*row_ref.keys, column_key), row_ref, atomic=True)
            case sem.Media():
                root = add(value, path, parent, atomic=False)
                for i, item in enumerate(value.items):
                    visit(item, root, i)
            case sem.Figure():
                root = add(value, path, parent, atomic=False)
                visit(value.media, root, 0)
                if value.caption is not None:
                    add(sem.Paragraph(value.caption), (*path, "@caption"), root, atomic=True)
            case sem.Fields():
                root = add(value, path, parent, atomic=False)
                for i, entry in enumerate(value.fields):
                    visit(entry, root, i)
            case sem.Field():
                root = add(value, path, parent, atomic=False)
                add(FieldPart("label", value.label), (*path, "label"), root, atomic=True)
                add(FieldPart("value", value.value), (*path, "value"), root, atomic=True)
            case sem.List():
                root = add(value, path, parent, atomic=False)
                for i, item in enumerate(value.items):
                    visit(item, root, i)
            case sem.Navigation():
                root = add(value, path, parent, atomic=False)
                for option in value.options:
                    add(NavigationChoice(value, option), (*path, option.key), root, atomic=True)
            case sem.Details():
                root = add(value, path, parent, atomic=False)
                add(DisclosureToggle(value), (*path, "@toggle"), root, atomic=True)
                for i, child in enumerate(value.children):
                    visit(child, root, i)
            case sem.Items():
                root = add(value, path, parent, atomic=False)
                for item in value.items:
                    branch = add(ItemBranch(value, item), (*path, item.key), root, atomic=False)
                    add(ItemSummary(value, item), (*branch.keys, "@summary"), branch, atomic=True)
                    for i, child in enumerate(item.children):
                        visit(child, branch, i)
                add(ItemBack(value), (*path, "@back"), root, atomic=True)
            case sem.ActionControls():
                root = add(value, path, parent, atomic=False)
                for i, control in enumerate(value.items):
                    visit(control, root, i)
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
                    sem.Group[RenderTargetT, ContextT]((), key="@description"),
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
                    visit(cast(Renderable[RenderTargetT, ContextT], child), root, i)
            case primitive.Panel():
                root = add(value, path, parent, atomic=False)
                for i, child in enumerate(value.children):
                    visit(child, root, i)
            case sem.ControlGroup():
                root = add(value, path, parent, atomic=False)
                if value.label is not None:
                    add(sem.Paragraph(value.label), (*path, "@label"), root, atomic=True)
                for i, control in enumerate(value.controls):
                    visit(control, root, i)
            case _ if isinstance(value, LAYOUT_CONTAINERS):
                root = add(value, path, parent, atomic=False)
                parts = layout_parts(value)
                if parts.heading is not None:
                    visit(parts.heading, root, 0, name="@heading")
                for i, child in enumerate(parts.children):
                    visit(child, root, i)
                if isinstance(value, sem.Region):
                    for part, children in (
                        (StructuralGroup("header"), parts.header),
                        (StructuralGroup("footer"), parts.footer),
                    ):
                        slot = add(part, (*path, f"@{part.part}"), root, atomic=False)
                        for i, child in enumerate(children):
                            visit(child, slot, i)
            case (
                sem.MediaItem()
                | primitive.GalleryItem()
                | primitive.File()
                | sem.Paragraph()
                | sem.Heading()
                | sem.Note()
                | sem.Code()
                | sem.Quote()
                | sem.Status()
                | sem.ProgressBar()
                | sem.Metric()
                | sem.Timestamp()
                | sem.ZonedTimestamp()
                | sem.Download()
                | TextPresentation()
                | AtomicNode()
                | primitive.RawItem()
                | sem.Column()
                | sem.ListItem()
                | sem.ActionControl()
                | sem.RoutedActionControl()
                | sem.Link()
                | sem.Toggle()
                | sem.FormTrigger()
                | sem.Choices()
                | sem.Entities()
                | sem.RoutedChoices()
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

    visit(node, None, 0)
    children: dict[SemanticRef, list[SemanticRef]] = {}
    for reference, unit in units.items():
        if consume is not None:
            consume()
        if unit.parent is not None:
            children.setdefault(unit.parent, []).append(reference)
    return SemanticGraph(
        MappingProxyType(units), MappingProxyType({ref: tuple(values) for ref, values in children.items()})
    )
