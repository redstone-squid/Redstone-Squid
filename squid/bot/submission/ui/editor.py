"""Section overview shared by the draft editor and the saved-build editor.

Sections stay on the main message while their Edit buttons open a modal or a child screen
(see `squid.bot.submission.ui.sections`). Subclasses supply the workspace and own the final
action: submitting a draft, or reviewing and saving changes to a saved build.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.sections import AnswerWorkspace, SectionEditorScreen, modal_chunks, present_chunk_form
from squid.core.i18n import tr


@dataclass(frozen=True)
class EditorSection:
    """A stable section key and its formatted heading and current values."""

    key: str
    text: sl.TextLike


class BuildEditorScreen[ResultT, WorkspaceT: AnswerWorkspace](sd.Screen[ResultT]):
    """Keep all sections visible while editing one section at a time."""

    workspace: WorkspaceT

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
        """Open the section's modal, or its own editor message if a modal cannot hold it."""
        section = self.workspace.section(section_id)
        if section is None:
            return
        chunks = modal_chunks(section)
        if len(chunks) == 1 and len(chunks[0].fields) == len(section.fields):
            await present_chunk_form(self.workspace, chunks[0], event)
            return
        await self.workspace.load_options(section.fields)
        await self.open_child(event, SectionEditorScreen(self.workspace, section_id), on_result=self._child_closed)

    async def _child_closed(self, event: sd.ScreenResultEvent[None]) -> None:
        """Child editors share state with the overview; closing needs no additional save."""
