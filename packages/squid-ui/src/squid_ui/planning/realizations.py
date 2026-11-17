"""Shared lossless realizations for the first composition vocabulary.

A realization is one way a target can draw a semantic node, such as a table as native rows or as
records. Tables retain keyed columns and cells until a target draws the selected form.
`squid_ui.planning.offers` proposes these realizations; neither module fits or paginates them.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from squid_i18n import tree
from squid_ui.authoring.presentations import (
    SemanticRef,
    Shortening,
)
from squid_ui.content.assets import Asset
from squid_ui.content.emoji import Emoji
from squid_ui.interactions import ActionMode
from squid_ui.native import NativeRealization
from squid_ui.planning import controls
from squid_ui.planning.text_presentations import (
    ResolvedText,
    join_text,
    slice_text,
)
from squid_ui.styles import ActionStyle, Color


@dataclass(frozen=True, slots=True)
class TextRealization:
    nodes: tuple[tree.Node, ...]
    provenance: ResolvedText | None = None
    role: Literal["paragraph", "heading", "note", "code", "quote"] = "paragraph"
    level: int = 2
    language: str = ""
    shortening: Shortening | None = None
    ellipsis: bool = True

    @property
    def visible_nodes(self) -> tuple[tree.Node, ...]:
        if self.shortening is None:
            return self.nodes
        operation = self.shortening
        length = sum(len(tree.plain(node)) for node in self.nodes)
        marker = (tree.literal("…"),) if self.ellipsis else ()
        if operation.keep == "head":
            return (*slice_text(self.nodes, 0, operation.max_chars), *marker)
        return (*marker, *slice_text(self.nodes, length - operation.max_chars, length))

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, nodes=slice_text(self.nodes, start, end))


@dataclass(frozen=True, slots=True)
class FieldRealization(TextRealization):
    label: tuple[tree.Node, ...] = ()
    value: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class StatusRealization(TextRealization):
    colour: Color | None = None
    tone: str = "neutral"
    emphasis: str = "normal"


@dataclass(frozen=True, slots=True)
class MetricRealization(TextRealization):
    label: tuple[tree.Node, ...] = ()
    value: str = ""


@dataclass(frozen=True, slots=True)
class TimeRealization(TextRealization):
    instant: str = ""
    display: str = ""
    style: str | None = None
    timezone: str | None = None
    prefix: tuple[tree.Node, ...] = ()
    native_prefix: str | None = None
    label: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressRealization(TextRealization):
    value: float = 0
    maximum: float = 1
    label: str | None = None


@dataclass(frozen=True, slots=True)
class FlowRealization:
    pass


@dataclass(frozen=True, slots=True)
class FigureRealization(FlowRealization):
    caption: bool


@dataclass(frozen=True, slots=True)
class FieldsRealization(FlowRealization):
    notice: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class DisclosureRealization(FlowRealization):
    open: bool


@dataclass(frozen=True, slots=True)
class PanelRealization:
    accent: Color | None
    framed: bool = True
    thumbnail: str | None = None
    heading: str = ""
    kind: Literal["block", "section", "article", "aside"] = "block"
    spoiler: bool = False
    tone: str | None = None


@dataclass(frozen=True, slots=True)
class ReferencedText:
    reference: SemanticRef
    nodes: tuple[tree.Node, ...]
    prefix: str = ""
    continuation: bool = False
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class TextSequenceRealization:
    items: tuple[ReferencedText, ...]
    separator: str = "\n"
    prefix: str = ""
    suffix: str = ""
    list_ordered: bool | None = None
    literal: bool = False
    notice: tuple[tree.Node, ...] = ()

    @property
    def display_items(self) -> tuple[ReferencedText, ...]:
        items: list[ReferencedText] = []
        for item in self.items:
            if item.continuation and items:
                items[-1] = replace(items[-1], nodes=join_text(items[-1].nodes, item.nodes))
            else:
                items.append(item)
        return tuple(items)

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, items=self.items[start:end])


@dataclass(frozen=True, slots=True)
class TableRecord:
    reference: SemanticRef
    cells: tuple[ReferencedText, ...]


class TableForm(StrEnum):
    NATIVE = "table"
    RECORDS = "records"
    MATRIX = "matrix"


@dataclass(frozen=True, slots=True)
class TableRealization:
    columns: tuple[ReferencedText, ...]
    rows: tuple[TableRecord, ...]
    form: TableForm

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, rows=self.rows[start:end])


@dataclass(frozen=True, slots=True)
class ImageRealization:
    source: str | Asset
    description: str
    spoiler: bool


@dataclass(frozen=True, slots=True)
class DownloadRealization:
    asset: Asset
    label: tuple[tree.Node, ...]
    description: tuple[tree.Node, ...]
    spoiler: bool


@dataclass(frozen=True, slots=True)
class ControlRealization:
    label: str
    action: str | None = None
    route: str | None = None
    url: str | None = None
    style: ActionStyle = ActionStyle.SECONDARY
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE
    emoji: Emoji | None = None
    current: bool | None = None


@dataclass(frozen=True, slots=True)
class RowRealization:
    pass


@dataclass(frozen=True, slots=True)
class ActionsRealization:
    """Ordered buttons that may occupy multiple target action rows."""

    navigation: bool = False
    columns: int | None = None
    grid_key: str | None = None


@dataclass(frozen=True, slots=True)
class SelectRealization:
    node: controls.Select | controls.RoutedSelect
    navigation: bool = False
    summaries: tuple[ReferencedText, ...] | None = None

    def fragment(self, start: int, end: int) -> Realization:
        return replace(
            self,
            node=replace(self.node, options=self.node.options[start:end]),
            summaries=self.summaries[start:end] if self.summaries is not None else None,
        )


@dataclass(frozen=True, slots=True)
class EntityRealization:
    node: controls.EntitySelect
    choices: tuple[controls.Option, ...] = ()


type Realization = (
    NativeRealization
    | TextSequenceRealization
    | ActionsRealization
    | RowRealization
    | SelectRealization
    | EntityRealization
    | TextRealization
    | FlowRealization
    | PanelRealization
    | TableRealization
    | ImageRealization
    | DownloadRealization
    | ControlRealization
)


def matrix_text(table: TableRealization) -> str:
    """Render an authored matrix with aligned columns inside one code block."""
    rows = [
        ["".join(tree.plain(node) for node in column.nodes) for column in table.columns],
        *[["".join(tree.plain(node) for node in cell.nodes) for cell in row.cells] for row in table.rows],
    ]
    widths = [max(len(row[index]) for row in rows) for index in range(len(table.columns))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows]
    return "```\n" + "\n".join(lines).replace("```", "``\u200b`") + "\n```"


def record_text(table: TableRealization) -> tuple[tuple[tree.Node, ...], ...]:
    """Keep each column label beside its value, including schemas with no records."""
    if not table.rows:
        schema: list[tree.Node] = []
        for i, column in enumerate(table.columns):
            if i:
                schema.append(tree.literal(" / "))
            schema.extend(column.nodes)
        return (tuple(schema),)
    result: list[tuple[tree.Node, ...]] = []
    for row in table.rows:
        nodes: list[tree.Node] = []
        for i, (column, cell) in enumerate(zip(table.columns, row.cells, strict=True)):
            if i:
                nodes.append(tree.literal("\n"))
            nodes.extend((*column.nodes, tree.literal(": "), *cell.nodes))
        result.append(tuple(nodes))
    return tuple(result)
