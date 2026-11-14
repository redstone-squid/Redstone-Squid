"""Retain semantic units and correspondence independently of target representations."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from squid_ui import semantic as sem
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.classic_content import CardSlot
from squid_ui.planning.interaction_semantics import (
    DisclosureToggle,
    Interaction,
    ItemBack,
    ItemSummary,
    NavigationChoice,
    interaction_changes,
)
from squid_ui.planning.roster_content import RosterLabel, RosterMembers, RosterSection
from squid_ui.planning.semantic_adaptation.handlers import ForwardSelection
from squid_ui.presentations import (
    Alternative,
    Alternatives,
    AtomicNode,
    Budget,
    Fidelity,
    InteractionChange,
    Omission,
    Replacement,
    SemanticRef,
    Shortening,
    TextPresentation,
)
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import Alt, Alts, Paginate
from squid_ui.rosters import RosterEntry, RosterSlot
from squid_ui.target_types import Renderable
from squid_ui.text import TextLike


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


def _key[RenderTargetT, ContextT](value: Renderable[RenderTargetT, ContextT] | EmbeddedValue[ContextT]) -> str | None:
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


def variant_alternatives(value: primitive.Variants[Any]) -> Alternatives[Any]:
    """Expose native ladders through checked coverage and interaction declarations."""
    choices: list[Alternative[Any]] = []
    for index, variant in enumerate(value.variants):
        if variant.fidelity is primitive.Fidelity.LOSSY and variant.contract is None:
            message = "lossy native Variants require an explicit Variant.contract"
            raise UnsupportedSemanticError(message)
        choices.append(
            Alternative(
                sem.Group(variant.nodes, key="@content"),
                key=f"rung-{index}",
                fidelity=variant.contract or Fidelity(),
                preference=index,
                correspondence=variant.correspondence,
                requires=variant.requires,
            )
        )
    return Alternatives(
        value.content or sem.Group(value.variants[0].nodes, key="@content"),
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
        value: Renderable[RenderTargetT, ContextT] | EmbeddedValue[ContextT],
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
            case (
                sem.Group()
                | sem.Stack()
                | sem.Cluster()
                | sem.Themed()
                | sem.Block()
                | sem.Section()
                | sem.Article()
                | sem.Aside()
                | sem.Region()
            ):
                root = add(value, path, parent, atomic=False)
                if isinstance(value, sem.Section | sem.Article):
                    visit(value.heading, root, 0, name="@heading")
                for i, child in enumerate(value.children):
                    visit(child, root, i)
                if isinstance(value, sem.Region):
                    for part, children in (
                        (StructuralGroup("header"), value.header),
                        (StructuralGroup("footer"), value.footer),
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


def interaction[RenderTargetT, ContextT](value: SemanticValue[RenderTargetT, ContextT]) -> Interaction[ContextT] | None:
    match value:
        case (
            sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.Toggle()
            | sem.FormTrigger()
            | sem.Choices()
            | sem.Entities()
            | NavigationChoice()
            | DisclosureToggle()
            | ItemSummary()
            | ItemBack()
            | sem.RoutedChoices()
            | primitive.Button()
            | primitive.RoutedButton()
            | primitive.LinkButton()
            | primitive.PremiumButton()
            | primitive.SelectMenu()
            | primitive.RoutedSelect()
            | primitive.EntitySelect()
        ):
            return value
        case _:
            return None


@dataclass(frozen=True, slots=True)
class CheckedAlternative[RenderTargetT, ContextT]:
    choice: Alternative[RenderTargetT, ContextT]
    source: SemanticGraph[RenderTargetT, ContextT]
    presentation: SemanticGraph[RenderTargetT, ContextT]
    coverage: Mapping[SemanticRef, frozenset[SemanticRef]]
    omitted: frozenset[SemanticRef]
    asserted: frozenset[SemanticRef]
    """Presentation units whose textual equivalence depends on the author's claim."""


def check_alternative[RenderTargetT, ContextT](
    group: Alternatives[RenderTargetT, ContextT],
    choice: Alternative[RenderTargetT, ContextT],
    *,
    consume: Callable[[], None] | None = None,
) -> CheckedAlternative[RenderTargetT, ContextT]:
    """Check references, coverage, and declared behavior changes, without proving prose meaning.

    This validates structural declarations only. Target evaluation must also check
    shortening bounds after localization and enforce inherited layout requirements.
    """
    source, presentation = semantic_graph(group.content, consume=consume), semantic_graph(choice.node, consume=consume)
    explicit: dict[SemanticRef, frozenset[SemanticRef]] = {}
    for mapping in choice.correspondence:
        target = presentation.resolve(mapping.presentation)
        if target in explicit:
            msg = f"duplicate correspondence for {target.keys!r}"
            raise LayoutInvariantError(msg)
        explicit[target] = frozenset(unit for item in mapping.content for unit in source.atoms(item))

    omitted: set[SemanticRef] = set()
    replaced: set[SemanticRef] = set()
    changed: dict[SemanticRef, set[str]] = {}
    shortenings: dict[SemanticRef, Shortening] = {}
    for effect in choice.fidelity.effects:
        affected = source.atoms(effect.source)
        if isinstance(effect, Omission):
            omitted.update(affected)
        elif isinstance(effect, Replacement):
            replaced.update(affected)
        elif isinstance(effect, InteractionChange):
            for unit in affected:
                if effect.attribute not in interaction_changes(interaction(source.units[unit].value), None):
                    msg = f"unknown interaction attribute {effect.attribute!r}"
                    raise LayoutInvariantError(msg)
                changed.setdefault(unit, set()).add(effect.attribute)
        elif isinstance(effect, Shortening):
            unit = source.resolve(effect.source)
            source_text(source.units[unit].value)
            if unit in shortenings:
                message = f"duplicate shortening declaration at {unit.keys!r}"
                raise LayoutInvariantError(message)
            shortenings[unit] = effect

    coverage: dict[SemanticRef, frozenset[SemanticRef]] = {}
    asserted: set[SemanticRef] = set()
    applied_shortening: set[SemanticRef] = set()
    for target in presentation.leaves:
        if consume is not None:
            consume()
        owners = [item for item in explicit if target.keys[: len(item.keys)] == item.keys]
        target_value = presentation.units[target].value
        if isinstance(target_value, TextPresentation):
            if owners:
                message = "text fragments carry their own correspondence"
                raise LayoutInvariantError(message)
            covered_units: set[SemanticRef] = set()
            for part in target_value.parts:
                if not isinstance(part, SemanticRef | Shortening):
                    continue
                source_ref = source.resolve(part.source if isinstance(part, Shortening) else part)
                source_text(source.units[source_ref].value)
                covered_units.add(source_ref)
                if isinstance(part, Shortening):
                    declared = shortenings.get(source_ref)
                    if declared is None or (declared.max_chars, declared.keep) != (part.max_chars, part.keep):
                        message = f"undeclared shortening operation at {source_ref.keys!r}"
                        raise LayoutInvariantError(message)
                    applied_shortening.add(source_ref)
            covered = frozenset(covered_units)
        elif owners:
            asserted.add(target)
            selected = max(owners, key=lambda item: len(item.keys))
            covered = explicit[selected]
        else:
            # Match named units across different presentation containers. An
            # unkeyed unit can only match the same positional path and kind.
            matches = [
                item
                for item in source.leaves
                if (item == target or (not target.keys[-1].startswith("@") and item.keys[-1] == target.keys[-1]))
                and type(source.units[item].value) is type(presentation.units[target].value)
            ]
            if target in matches:
                matches = [target]
            if len(matches) != 1:
                msg = f"presentation unit {target.keys!r} needs an explicit correspondence"
                raise LayoutInvariantError(msg)
            covered = frozenset(matches)
            if source.units[matches[0]].value != target_value:
                asserted.add(target)
        coverage[target] = covered
        target_behavior = interaction(presentation.units[target].value)
        for unit in covered:
            if unit in replaced:
                continue
            changed_attributes = interaction_changes(interaction(source.units[unit].value), target_behavior)
            if changed_attributes - changed.get(unit, set()):
                msg = f"undeclared interaction change at {unit.keys!r}"
                raise LayoutInvariantError(msg)

    if applied_shortening != shortenings.keys():
        message = "shortening declarations require source-referenced text fragments"
        raise LayoutInvariantError(message)
    retained = frozenset(unit for covered in coverage.values() for unit in covered)
    missing = source.leaves - retained
    if missing != omitted | (replaced - retained):
        msg = f"declared omissions do not match missing semantic units: {missing ^ omitted}"
        raise LayoutInvariantError(msg)
    checked = CheckedAlternative(
        choice, source, presentation, MappingProxyType(coverage), frozenset(omitted), frozenset(asserted)
    )
    _check_boundaries(checked)
    return checked


def source_text[RenderTargetT, ContextT](value: SemanticValue[RenderTargetT, ContextT]) -> TextLike:
    """Return one source text unit; containers need references to their individual parts."""
    match value:
        case (
            sem.Paragraph()
            | sem.Heading()
            | sem.Note()
            | sem.Code()
            | sem.ListItem()
            | TableCell()
            | FieldPart()
            | primitive.Text()
            | primitive.Heading()
            | primitive.Footer()
            | primitive.Code()
        ):
            return value.content
        case sem.Column():
            return value.heading
        case _:
            message = f"a text fragment must reference one text unit, not {type(value).__name__}"
            raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class SourceScope[RenderTargetT, ContextT]:
    """Translate one presentation's references back through enclosing alternatives."""

    checked: CheckedAlternative[RenderTargetT, ContextT]
    group: SemanticRef
    parent: SourceScope[RenderTargetT, ContextT] | None = None

    def source_references(self, reference: SemanticRef) -> frozenset[SemanticRef]:
        scoped = SemanticRef((*self.group.keys, *reference.keys))
        return self.parent.references(scoped) if self.parent is not None else frozenset({scoped})

    def references(self, presentation: SemanticRef) -> frozenset[SemanticRef]:
        sources = {
            source
            for target, covered in self.checked.coverage.items()
            if target.keys[: len(presentation.keys)] == presentation.keys
            for source in covered
        }
        return frozenset(result for source in sources for result in self.source_references(source))

    def page_boundary(self, presentation: SemanticRef) -> SemanticRef:
        """Locate a preserved pager in the authored graph, across nested alternatives."""
        value = self.checked.presentation.units[presentation].value
        if not isinstance(value, sem.Region | sem.Paged | sem.List | sem.Navigation | sem.Items | sem.Grid) and not (
            isinstance(value, primitive.Lines | primitive.Text | primitive.Heading | primitive.Footer | primitive.Code)
            and isinstance(value.overflow, Paginate)
        ):
            message = "page boundary resolution needs a pagination scope"
            raise LayoutInvariantError(message)
        covered = frozenset(
            source
            for target, sources in self.checked.coverage.items()
            if target.keys[: len(presentation.keys)] == presentation.keys
            for source in sources
        )
        matches = [
            reference
            for reference, unit in self.checked.source.units.items()
            if type(unit.value) is type(value)
            and _key(unit.value) == _key(value)
            and covered <= self.checked.source.atoms(reference)
        ]
        if len(matches) != 1:
            message = "an alternate pager needs one unambiguous authored boundary"
            raise LayoutInvariantError(message)
        scoped = SemanticRef((*self.group.keys, *matches[0].keys))
        return self.parent.page_boundary(scoped) if self.parent is not None else scoped


def budget_floor[RenderTargetT, ContextT](
    graph: SemanticGraph[RenderTargetT, ContextT],
    reference: SemanticRef,
    inherited: Iterable[SemanticValue[RenderTargetT, ContextT]] = (),
) -> int:
    """Return the hard character reservation after explicit best-effort permission."""
    unit = graph.units[reference]
    if isinstance(unit.value, primitive.Budget):
        if unit.value.best_effort:
            return 0
    elif not isinstance(unit.value, sem.Budgeted) or isinstance(unit.value.node, sem.BestEffort):
        return 0
    if any(isinstance(value, sem.BestEffort) for value in inherited):
        return 0
    parent = unit.parent
    while parent is not None:
        ancestor = graph.units[parent]
        if isinstance(ancestor.value, sem.BestEffort):
            return 0
        parent = ancestor.parent
    return unit.value.minimum


def _check_boundaries[RenderTargetT, ContextT](checked: CheckedAlternative[RenderTargetT, ContextT]) -> None:
    """Preserve hard layout scopes even when presentations rearrange their contents."""
    for reference, unit in checked.source.units.items():
        constraint = unit.value
        if not isinstance(
            constraint,
            Budget
            | sem.Budgeted
            | sem.Unbreakable
            | sem.KeepWithNext
            | sem.Paged
            | sem.Region
            | sem.List
            | sem.Navigation
            | sem.Items
            | sem.Grid
            | primitive.Row
            | primitive.Section
            | primitive.Panel
            | primitive.Gallery
            | primitive.Card
            | primitive.Content
            | RosterSection
            | primitive.Budget
            | primitive.Break,
        ) and not (
            isinstance(
                constraint, primitive.Lines | primitive.Text | primitive.Heading | primitive.Footer | primitive.Code
            )
            and isinstance(constraint.overflow, Paginate)
        ):
            continue
        inside = checked.source.atoms(reference)
        targets = frozenset(target for target, covered in checked.coverage.items() if covered & inside)
        if not targets:
            continue
        if any(checked.coverage[target] - inside for target in targets):
            message = f"alternative merges content across a {type(constraint).__name__} boundary at {reference.keys!r}"
            raise LayoutInvariantError(message)
        matches = [
            (target, other.value)
            for target, other in checked.presentation.units.items()
            if type(other.value) is type(constraint) and checked.presentation.atoms(target) == targets
        ]
        preserved = False
        for target, other in matches:
            match constraint, other:
                case Budget(), Budget():
                    preserved = all(
                        axis in other.limits and other.limits[axis] <= cap for axis, cap in constraint.limits.items()
                    )
                case (sem.Budgeted(), sem.Budgeted()) | (primitive.Budget(), primitive.Budget()):
                    preserved = (constraint.minimum, constraint.preferred, constraint.stretch) == (
                        other.minimum,
                        other.preferred,
                        other.stretch,
                    ) and budget_floor(checked.presentation, target) >= budget_floor(checked.source, reference)
                case primitive.Break(), primitive.Break():
                    preserved = (not constraint.unbreakable or other.unbreakable) and (
                        not constraint.keep_with_next or other.keep_with_next
                    )
                case sem.Paged(), sem.Paged():
                    preserved = (
                        constraint.key,
                        constraint.page,
                        constraint.chars,
                        constraint.min_fill,
                        constraint.widows,
                        constraint.footer,
                    ) == (other.key, other.page, other.chars, other.min_fill, other.widows, other.footer)
                case sem.Region(), sem.Region():
                    preserved = (constraint.key, constraint.page, constraint.header, constraint.footer) == (
                        other.key,
                        other.page,
                        other.header,
                        other.footer,
                    )
                case sem.Items(), sem.Items():
                    preserved = (constraint.key, constraint.opened) == (other.key, other.opened)
                case sem.Navigation(), sem.Navigation():
                    preserved = (constraint.key, constraint.current) == (other.key, other.current)
                case sem.Grid(), sem.Grid():
                    preserved = (constraint.key, constraint.columns, tuple(cell.key for cell in constraint.cells)) == (
                        other.key,
                        other.columns,
                        tuple(cell.key for cell in other.cells),
                    )
                case sem.List(), sem.List():
                    preserved = (constraint.key, constraint.page, constraint.page_size) == (
                        other.key,
                        other.page,
                        other.page_size,
                    )
                case (
                    (
                        primitive.Lines()
                        | primitive.Text()
                        | primitive.Heading()
                        | primitive.Footer()
                        | primitive.Code()
                    ),
                    (
                        primitive.Lines()
                        | primitive.Text()
                        | primitive.Heading()
                        | primitive.Footer()
                        | primitive.Code()
                    ),
                ):
                    preserved = constraint.overflow == other.overflow
                case (sem.Unbreakable(), sem.Unbreakable()) | (sem.KeepWithNext(), sem.KeepWithNext()):
                    preserved = True
                case primitive.Row(), primitive.Row():
                    preserved = True
                case primitive.Section(), primitive.Section():
                    preserved = len(constraint.texts) == len(other.texts)
                case RosterSection(), RosterSection():
                    preserved = constraint == other
                case primitive.Card(), primitive.Card():
                    preserved = (
                        constraint.url,
                        constraint.accent,
                        constraint.image,
                        constraint.thumbnail,
                        constraint.timestamp,
                        tuple(entry.inline for entry in constraint.fields),
                        constraint.footer.icon_url if constraint.footer else None,
                        (constraint.author.url, constraint.author.icon_url) if constraint.author else None,
                    ) == (
                        other.url,
                        other.accent,
                        other.image,
                        other.thumbnail,
                        other.timestamp,
                        tuple(entry.inline for entry in other.fields),
                        other.footer.icon_url if other.footer else None,
                        (other.author.url, other.author.icon_url) if other.author else None,
                    )
                case primitive.Content(), primitive.Content():
                    preserved = True
                case primitive.Gallery(), primitive.Gallery():
                    preserved = len(constraint.items) == len(other.items)
                case primitive.Panel(), primitive.Panel():
                    preserved = (constraint.accent, constraint.spoiler) == (other.accent, other.spoiler)
            if preserved:
                break
        if not preserved:
            message = f"alternative must preserve the {type(constraint).__name__} boundary at {reference.keys!r}"
            raise LayoutInvariantError(message)
