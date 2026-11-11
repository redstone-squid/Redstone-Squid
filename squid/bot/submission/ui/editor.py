"""Section overview shared by new submissions and edits to stored builds.

Sections stay on the main message while their Edit buttons open a modal or a
child screen. Subclasses own persistence and the final submit or save action.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import squid_ui as sl
import squid_ui_discord as sd
from squid.core.i18n import tr


@dataclass(frozen=True)
class EditorSection:
    """A stable section key and its formatted heading and current values."""

    key: str
    text: sl.TextLike


class BuildEditorScreen[ResultT](sd.Screen[ResultT]):
    """Keep all sections visible while editing one section at a time."""

    def section_overview(self, sections: Sequence[EditorSection]) -> list[Any]:
        return [
            sd.v2.section(
                section.text,
                accessory=sd.v2.button(
                    tr(t"draft-editor-edit = Edit"),
                    partial(self._edit_section, section.key),
                    key=f"edit_{section.key}",
                ),
            )
            for section in sections
        ]

    async def _edit_section(self, section_id: str, event: sd.PressEvent) -> None:
        raise NotImplementedError

    async def _child_closed(self, event: sd.ScreenResultEvent[None]) -> None:
        """Child editors share state with the overview; closing needs no additional save."""
