"""The submission overview also serves edits to stored builds."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.editor import BuildEditorScreen
from squid.bot.submission.ui.views import EDIT_FIELDS, BuildEditScreen
from squid.builds.application import BuildService
from squid.builds.models import DoorBuild, ExtenderBuild, OtherBuild
from squid_ui.testing import press_event, submit_event
from squid_ui_discord.testing import assert_within_limits, commit_render


def editor(build: DoorBuild | ExtenderBuild | OtherBuild) -> BuildEditScreen:
    return BuildEditScreen(
        build,
        cast(BuildService, AsyncMock()),
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(return_value=sl.paragraph("Preview")),
        refresh_posts=AsyncMock(),
    )


@pytest.mark.parametrize("build_type", [DoorBuild, ExtenderBuild, OtherBuild])
async def test_every_applicable_field_is_in_one_named_section(build_type: Any) -> None:
    build = build_type(id=1)
    screen = editor(build)
    assert isinstance(screen, BuildEditorScreen)
    assert issubclass(DraftEditorScreen, BuildEditorScreen)
    keys = [item.key for _, _, items in screen.sections() for item in items]
    assert len(keys) == len(set(keys))
    assert set(keys) == {field.key for field in EDIT_FIELDS if field.applies_to(build)}
    assert all(len(items) <= 5 for _, _, items in screen.sections())
    await screen.projection.reload()
    root = sd.MessageRoot(screen, access=sd.Owner(7), timeout=None)
    assert_within_limits(commit_render(root))


async def test_editing_one_section_keeps_other_staged_values_and_requires_review_again() -> None:
    build = DoorBuild(id=1, version_spec="1.20")
    screen = editor(build)
    screen.stage("version_spec", "1.21")
    screen.confirming = True

    await screen._edited(("creators_ign",), submit_event({"creators_ign": "Alice, Bob"}))

    values = {item.key: item.value for item in screen.items}
    assert values["version_spec"] == "1.21"
    assert values["creators_ign"] == ["Alice", "Bob"]
    assert not screen.confirming
    assert build.version_spec == "1.20"
    assert build.creators_ign == []


async def test_preview_includes_staged_values_without_changing_the_stored_build() -> None:
    build = DoorBuild(id=1, version_spec="1.20")
    screen = editor(build)
    screen.stage("version_spec", "1.21")
    screen.open_child = AsyncMock()  # type: ignore[method-assign]

    await screen._preview(cast(sd.PressEvent, press_event()))

    renderer = cast(AsyncMock, screen._render_build)
    preview = renderer.call_args.args[0]
    assert preview.version_spec == "1.21"
    assert preview is not build
    assert build.version_spec == "1.20"
    screen.open_child.assert_awaited_once()
