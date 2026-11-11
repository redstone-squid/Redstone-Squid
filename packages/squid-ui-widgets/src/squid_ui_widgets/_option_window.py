"""Machine-resolved picker windows shared by rendering and selection transitions."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid_ui.factories import action_controls, paragraph
from squid_ui.planning.cursors import content_fingerprint
from squid_ui.semantic import Choice, LayoutNode
from squid_ui_widgets._actions import MachineKeySegment, WindowSelection
from squid_ui_widgets.drivers import MachineControls


@dataclass(frozen=True, slots=True)
class OptionWindow:
    """One page of available choices and the scope of its replacement submission.

    The revision covers the ordered available keys and window size. Labels may change
    without invalidating a submission; membership, availability, and order may not.
    This is a stale-input check, not authentication of a routed action.
    """

    choices: tuple[Choice, ...]
    page: int
    pages: int
    revision: str

    @classmethod
    def resolve(cls, choices: Sequence[Choice], page: int, per: int) -> OptionWindow:
        if per < 1 or per > 25:
            message = "option window size must be between 1 and 25"
            raise ValueError(message)
        available = tuple(choice for choice in choices if choice.available)
        pages = max(1, (len(available) + per - 1) // per)
        page = max(0, min(page, pages - 1))
        revision = content_fingerprint([str(per), *(choice.key for choice in available)])
        return cls(available[page * per : (page + 1) * per], page, pages, revision)

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(choice.key for choice in self.choices)

    def selection_action(self, group: MachineKeySegment) -> str:
        """Identify the rendered window even if the machine later turns a page."""
        return WindowSelection(group, self.page, self.revision).encode()

    def matches(self, scope: WindowSelection) -> bool:
        return self.page == scope.page and self.revision == scope.revision

    def navigation(self, controls: MachineControls, key: str, *, previous: str, next: str) -> tuple[LayoutNode, ...]:
        """Render the resolved page's footer and driver-bound movement controls."""
        if self.pages == 1:
            return ()
        return (
            paragraph(controls.chrome.page_footer(self.page + 1, self.pages)),
            action_controls(
                controls.action_control(
                    controls.chrome.previous,
                    previous,
                    key=f"{key}.previous",
                    available=self.page > 0,
                ),
                controls.action_control(
                    controls.chrome.next,
                    next,
                    key=f"{key}.next",
                    available=self.page < self.pages - 1,
                ),
                key=f"{key}.pager",
            ),
        )
