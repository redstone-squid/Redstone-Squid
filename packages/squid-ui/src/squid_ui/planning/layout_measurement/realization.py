"""Realize concrete primitives and enforce their local shape limits."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.layout_measurement.diagnostics import (
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    note,
)
from squid_ui.planning.layout_measurement.model import (
    MeasuredCard,
    MeasuredCardField,
    MeasuredContent,
    MeasuredGroup,
    MeasuredPanel,
    MeasuredSection,
    MeasuredText,
    MeasuredTime,
    MeasuredZonedTime,
    Realized,
)
from squid_ui.planning.layout_measurement.text import BudgetRegion, TextBearing, TextUnit, make_unit, trim_keep
from squid_ui.planning.limits import LIMITS, Axis, MessageLimits
from squid_ui.planning.resolved import optional_text as resolved_optional_text
from squid_ui.planning.resolved import text as resolved_text
from squid_ui.primitives.nodes import (
    Boundary,
    Break,
    Budget,
    Button,
    Card,
    CardText,
    Code,
    Content,
    EntitySelect,
    File,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    PremiumButton,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
    Time,
    Variants,
    ZonedTime,
    card_text,
)


@dataclass(slots=True)
class Builder:
    limits: MessageLimits = LIMITS
    notes: list[SolveNote] = field(default_factory=list)
    units: list[TextUnit] = field(default_factory=list)
    raw_text_cost: dict[Axis, int] = field(default_factory=dict)
    """Text no overflow policy can shrink, per axis: timestamps and prepared native items."""
    budgets: list[BudgetRegion] = field(default_factory=list)
    axis: Axis = Axis.DISPLAY_TEXT
    """The pool text realized right now draws from; target shape moves it, nothing else."""

    def charge(self, characters: int) -> None:
        self.raw_text_cost[self.axis] = self.raw_text_cost.get(self.axis, 0) + characters

    def unit(self, node: TextBearing, slot: MeasuredText) -> None:
        made = make_unit(node, slot, len(self.units), self.axis)
        if made is not None:
            self.units.append(made)

    @contextmanager
    def pool(self, axis: Axis) -> Iterator[None]:
        """Realize everything inside this block against another text pool."""
        previous = self.axis
        self.axis = axis
        try:
            yield
        finally:
            self.axis = previous

    def slot(self, value: CardText | None, cap: int, what: str) -> MeasuredText | None:
        """Realize one card text slot, clamping it to its own local cap first."""
        if value is None:
            return None
        node = card_text(value)
        content = resolved_text(node.content).strip()
        if not content:
            return None
        if len(content) > cap:
            self.notes.append(
                note(
                    SolveNoteCode.CLAMP_EMBED_TEXT,
                    f"{what} clamped from {len(content)} to {cap}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            content = trim_keep(content, cap, "head")
        slot = MeasuredText()
        self.unit(replace(node, content=content), slot)
        return slot

    def card(self, node: Card) -> MeasuredCard:
        embeds = self.limits.embeds
        if embeds is None:
            message = "a Card cannot be realized in a message mode that has no embeds"
            raise LayoutInvariantError(message)
        fields = node.fields[: embeds.fields]
        if len(fields) != len(node.fields):
            self.notes.append(
                note(
                    SolveNoteCode.CLAMP_EMBED_FIELDS,
                    f"card holds {len(node.fields)} fields; keeping {len(fields)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
        realized_fields: list[MeasuredCardField] = []
        for field_node in fields:
            name = self.slot(field_node.name, embeds.field_name, "field name")
            value = self.slot(field_node.value, embeds.field_value, "field value")
            if name is None or value is None:
                message = "a CardField needs a non-empty name and value after trimming"
                raise LayoutInvariantError(message)
            realized_fields.append(MeasuredCardField(name, value, field_node.inline))
        return MeasuredCard(
            title=self.slot(node.title, embeds.title, "embed title"),
            url=node.url,
            blocks=self.realize_children(node.children),
            fields=realized_fields,
            footer=None if node.footer is None else self.slot(node.footer.text, embeds.footer, "embed footer"),
            footer_icon=None if node.footer is None else node.footer.icon_url,
            author=None if node.author is None else self.slot(node.author.name, embeds.author, "embed author"),
            author_url=None if node.author is None else node.author.url,
            author_icon=None if node.author is None else node.author.icon_url,
            accent=node.accent,
            image=node.image,
            thumbnail=node.thumbnail,
            timestamp=node.timestamp,
        )

    def _clamp_button[ButtonT: Button | LinkButton | RoutedButton](self, button: ButtonT) -> ButtonT:
        label = resolved_optional_text(button.label)
        if label is None or len(label) <= self.limits.components.button_label:
            return button
        self.notes.append(
            note(
                SolveNoteCode.CLAMP_BUTTON_LABEL,
                f"button label clamped from {len(label)}",
                SolveNoteSeverity.CLAMP,
            )
        )
        return replace(button, label=trim_keep(label, self.limits.components.button_label, "head"))

    def _clamp_select[SelectT: SelectMenu | RoutedSelect](self, select: SelectT) -> SelectT:
        limits = self.limits
        options = select.options
        if len(options) > limits.components.select_options:
            self.notes.append(
                note(
                    SolveNoteCode.CLAMP_SELECT_OPTIONS,
                    f"{len(options)} select options clamped to {limits.components.select_options}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            options = options[: limits.components.select_options]
        clamped_options = []
        for option in options:
            label = trim_keep(resolved_text(option.label), limits.components.option_label, "head")
            value = option.value[: limits.components.option_value]
            description = resolved_optional_text(option.description)
            if description is not None and len(description) > limits.components.option_description:
                description = trim_keep(description, limits.components.option_description, "head")
            if (label, value, description) != (option.label, option.value, option.description):
                self.notes.append(
                    note(SolveNoteCode.CLAMP_SELECT_OPTION_TEXT, "select option text clamped", SolveNoteSeverity.CLAMP)
                )
                option = Option(
                    label=label,
                    value=value,
                    description=description,
                    default=option.default,
                    emoji=option.emoji,
                )
            clamped_options.append(option)
        placeholder = resolved_optional_text(select.placeholder)
        if placeholder is not None and len(placeholder) > limits.components.select_placeholder:
            self.notes.append(
                note(
                    SolveNoteCode.CLAMP_SELECT_PLACEHOLDER,
                    f"select placeholder clamped from {len(placeholder)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            placeholder = trim_keep(placeholder, limits.components.select_placeholder, "head")
        return replace(
            select,
            options=tuple(clamped_options),
            placeholder=placeholder,
            max_values=min(select.max_values, len(clamped_options) or 1),
        )

    def _clamp_entity_select(self, select: EntitySelect) -> EntitySelect:
        placeholder = resolved_optional_text(select.placeholder)
        if placeholder is not None and len(placeholder) > self.limits.components.select_placeholder:
            self.notes.append(
                note(
                    SolveNoteCode.CLAMP_SELECT_PLACEHOLDER,
                    f"select placeholder clamped from {len(placeholder)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            placeholder = trim_keep(placeholder, self.limits.components.select_placeholder, "head")
        return replace(select, placeholder=placeholder)

    def realize_children(self, nodes: Sequence[Node]) -> list[Realized]:
        return [self.realize(node) for node in nodes]

    def realize(self, node: Node) -> Realized:
        match node:
            case Text() | Heading() | Footer() | Code() | Lines():
                slot = MeasuredText()
                self.unit(node, slot)
                return slot
            case Time(instant=instant, style=style, prefix=prefix):
                unix = int(instant.timestamp())
                self.charge(len(prefix or "") + len(f"<t:{unix}:{style}>"))
                return MeasuredTime(instant, style, prefix)
            case ZonedTime(value=value, prefix=prefix):
                self.charge(len(prefix or "") + len(value.isoformat()))
                return MeasuredZonedTime(value, prefix)
            case Section(texts=texts, accessory=accessory):
                if len(texts) > 3:
                    self.notes.append(
                        note(
                            SolveNoteCode.CLAMP_SECTION_TEXTS,
                            f"section holds {len(texts)} texts; keeping 3",
                            SolveNoteSeverity.CLAMP,
                        )
                    )
                    texts = texts[:3]
                slots: list[MeasuredText] = []
                for text_node in texts:
                    slot = MeasuredText()
                    self.unit(text_node, slot)
                    slots.append(slot)
                if isinstance(accessory, RawItem):
                    self.charge(accessory.text_cost)
                return MeasuredSection(texts=slots, accessory=accessory)
            case Content(content=text, overflow=overflow, priority=priority):
                slot = MeasuredText()
                with self.pool(Axis.CONTENT_TEXT):
                    self.unit(Text(text, overflow=overflow, priority=priority), slot)
                return MeasuredContent(slot)
            case Card():
                with self.pool(Axis.EMBED_TEXT):
                    return self.card(node)
            case Panel(children=children, accent=accent, spoiler=spoiler):
                return MeasuredPanel(children=self.realize_children(children), accent=accent, spoiler=spoiler)
            case Budget(
                children=children,
                minimum=minimum,
                preferred=preferred,
                stretch=stretch,
                best_effort=best_effort,
            ):
                first = len(self.units)
                realized = self.realize_children(children)
                self.budgets.append(BudgetRegion(tuple(self.units[first:]), minimum, preferred, stretch, best_effort))
                return MeasuredGroup(realized)
            case Break(children=children):
                return MeasuredGroup(self.realize_children(children))
            case Gallery(items=items):
                if len(items) > 10:
                    self.notes.append(
                        note(
                            SolveNoteCode.CLAMP_GALLERY_ITEMS,
                            f"gallery holds {len(items)} items; keeping 10",
                            SolveNoteSeverity.CLAMP,
                        )
                    )
                    node = Gallery(items=items[:10])
                return node
            case Row(items=items):
                self.charge(sum(item.text_cost for item in items if isinstance(item, RawItem)))
                clamped = tuple(
                    self._clamp_button(item) if isinstance(item, Button | LinkButton | RoutedButton) else item
                    for item in items
                )
                return Row(items=clamped)
            case SelectMenu() | RoutedSelect():
                return self._clamp_select(node)
            case EntitySelect():
                return self._clamp_entity_select(node)
            case RawItem(text_cost=text_cost):
                self.charge(text_cost)
                return node
            case File() | Sep() | Thumbnail() | PremiumButton() | Button() | LinkButton():
                return node
            case Boundary():
                message = "Boundary must be expanded before solving"
                raise ValueError(message)
            case Variants():
                message = "Variants must be resolved before measuring; plan() owns that choice"
                raise LayoutInvariantError(message)
            case _:
                message = f"{type(node).__name__} must be normalized before measuring"
                raise LayoutInvariantError(message)
