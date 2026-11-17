"""Static examples for the proposed API; this module cannot run."""

from dataclasses import dataclass
from typing import assert_type, override

from presentation_api import EventReader, HistoryField, ValueBinding, history, presentation

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.runtime.histories import History


@dataclass(frozen=True)
class PriceRange:
    minimum: int
    maximum: int


class CustomSelection:
    def clear(self) -> None:
        raise NotImplementedError


def price_picker(value: ValueBinding[PriceRange]) -> sl.LayoutNode:
    raise NotImplementedError


def integer_binding(value: ValueBinding[int]) -> None:
    raise NotImplementedError


class Reports(sd.Screen[None]):
    edits = history()
    work_lost = presentation.toggle("Work lost only")
    selected = presentation.choices(minimum=0, maximum=25, record=edits, record_label="Change selection", persist=True)
    status = presentation.choices(sl.choice("Pending", key="pending"))
    price = presentation(PriceRange(10, 100))
    search = presentation("")
    enabled = presentation(initial=False)
    draft = presentation(factory=CustomSelection, opaque=True)
    custom_range = presentation(
        factory=lambda: PriceRange(0, 10), record=edits, record_label="Change range", persist=True
    )

    @override
    def render(self) -> sl.DocumentLike[sd.ComponentsV2Target]:
        assert_type(self.work_lost.value, bool)
        assert_type(self.selected.value, tuple[str, ...])
        assert_type(self.price.value, PriceRange)
        assert_type(self.custom_range.value, PriceRange)
        assert_type(self.draft.value, CustomSelection)
        assert_type(self.edits, History)
        return (
            sl.action_controls(self.work_lost.bind, key="filters"),
            sl.action_controls(self.work_lost.bind(available=False), key="disabled"),
            self.selected.bind(sl.choice("One", key="one")),
            self.status.bind,
            price_picker(self.price.bind),
            sl.paragraph(f"Selected: {len(self.selected.value)}"),
        )

    def read_selection(self, event: EventReader) -> None:
        assert_type(event.read(self.selected), tuple[str, ...])
        assert_type(event.read(self.work_lost), bool)
        draft = event.read(self.draft)
        assert_type(draft, CustomSelection)
        draft.clear()
        self.mutated(draft)


assert_type(Reports.edits, HistoryField)


# Negative probes: each must fail when uncommented.
# sl.Document((Reports().selected.bind,))
# price_picker(Reports().work_lost.bind)
# price_picker(Reports().search.bind)
# presentation.toggle("Invalid", initial="yes")
# Reports().selected.bind(sl.paragraph("Wrong entry type"))
# Reports().selected.bind()
# integer_binding(Reports().enabled.bind)
# Reports().work_lost = Reports().work_lost
# presentation(factory=42)
# presentation(0, factory=lambda: 1)
# presentation.toggle("Invalid", record="edits")
# presentation.toggle("Invalid", opaque=True)
