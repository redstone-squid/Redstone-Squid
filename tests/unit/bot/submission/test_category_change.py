"""The moderator reviews a new category and its subtype facts before saving."""

from typing import cast
from unittest.mock import AsyncMock

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.submission.ui.category_change import BuildCategoryEditor
from squid.core.builds.application.category_edits import BuildCategoryChange
from squid.core.builds.application.edit_schema import ExtenderPatch
from squid.core.builds.domain import BuildCategory
from squid.core.builds.models import DoorBuild
from squid.core.edits.application import EditRunner
from squid_ui.ext.discord.testing import assert_within_limits, commit_render, payload_texts
from tests.support.build_edit_runner import build_edit_actor


def test_category_preview_keeps_old_facts_visible_and_shows_destination_facts() -> None:
    screen = BuildCategoryEditor(
        DoorBuild(id=7, width=8, door_width=3),
        cast(EditRunner, AsyncMock()),
        build_edit_actor,
        AsyncMock(),
    )
    with sl.runtime.transaction():
        screen.change = BuildCategoryChange(category=BuildCategory.EXTENDER, extender=ExtenderPatch(extension_length=5))
    payload = commit_render(sd.MessageRoot(screen, access=sd.Owner(7), timeout=None))
    assert_within_limits(payload)
    text = "\n".join(payload_texts(payload))
    assert "Current category: Door" in text
    assert "Opening width: 3" in text
    assert "New category: Extender" in text
    assert "Extension length: 5" in text
