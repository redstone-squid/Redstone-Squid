"""Static examples for the proposed API; this module cannot run."""

from dataclasses import dataclass
from typing import assert_type, override

from presentation_api import EventReader, ValueBinding, presentation

import squid_ui as sl
import squid_ui.ext.discord as sd


@dataclass(frozen=True)
class PriceRange:
    minimum: int
    maximum: int


def price_picker(value: ValueBinding[PriceRange]) -> sl.LayoutNode:
    raise NotImplementedError


def integer_binding(value: ValueBinding[int]) -> None:
    raise NotImplementedError


class Reports(sd.Screen[None]):
    work_lost = presentation.toggle("Work lost only")
    selected = presentation.choices(minimum=0, maximum=25)
    status = presentation.choices(sl.choice("Pending", key="pending"))
    price = presentation(PriceRange(10, 100))
    search = presentation("")
    enabled = presentation(initial=False)

    @override
    def render(self) -> sl.DocumentLike[sd.ComponentsV2Target]:
        assert_type(self.work_lost.value, bool)
        assert_type(self.selected.value, tuple[str, ...])
        assert_type(self.price.value, PriceRange)
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


# Negative probes: each must fail when uncommented.
# sl.Document((Reports().selected.bind,))
# price_picker(Reports().work_lost.bind)
# price_picker(Reports().search.bind)
# presentation.toggle("Invalid", initial="yes")
# Reports().selected.bind(sl.paragraph("Wrong entry type"))
# Reports().selected.bind()
# integer_binding(Reports().enabled.bind)
# Reports().work_lost = Reports().work_lost
