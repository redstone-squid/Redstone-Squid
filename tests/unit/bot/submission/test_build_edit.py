"""Editing a saved build through the build form's sections, staged until the user applies them."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.build_edit import BuildEditScreen
from squid.builds.application import BuildService
from squid.builds.domain import Status, UnresolvedTaxonomy
from squid.builds.domain.credits import name_credits
from squid.builds.models import Build, DoorBuild, ExtenderBuild, OtherBuild
from squid.core.sentinel import UNSET
from squid_ui.testing import press_event, texts
from squid_ui_discord.testing import assert_within_limits, commit_render
from tests.support.build_edit_runner import BuildEditRecorder, build_edit_actor, edit_workspace


def editor(build: Build) -> BuildEditScreen:
    return BuildEditScreen(
        build,
        cast(BuildService, AsyncMock()),
        edit_workspace(build),
        edits=BuildEditRecorder(),
        actor=build_edit_actor,
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(return_value=sl.paragraph("Preview")),
        refresh_posts=AsyncMock(),
    )


def rendered(screen: BuildEditScreen) -> str:
    return "\n".join(texts(screen.render()))


def _door(**values: Any) -> DoorBuild:
    if "creators_ign" in values:
        values["credits"] = name_credits(values.pop("creators_ign"), legacy=True)
    return DoorBuild(id=1, submission_status=Status.CONFIRMED, submitter_account_id=1, **values)


@pytest.mark.parametrize("build_type", [DoorBuild, ExtenderBuild, OtherBuild])
async def test_every_category_renders_its_form_sections_within_discord_limits(build_type: Any) -> None:
    screen = editor(build_type(id=1, submission_status=Status.CONFIRMED, submitter_account_id=1))
    await screen.projection.reload()

    assert screen.workspace.sections
    root = sd.MessageRoot(screen, access=sd.Owner(7), timeout=None)
    assert_within_limits(commit_render(root))


async def test_changes_stay_staged_and_undo_steps_back_through_them() -> None:
    build = _door(version_spec="1.20")
    workspace = editor(build).workspace

    with sl.runtime.transaction():
        await workspace.save_values({"version_compatibility": "1.21"})
    with sl.runtime.transaction():
        await workspace.save_values({"creators": ["Alice", "Bob"]})

    assert workspace.patch().attributes() == {"version_spec": "1.21", "creators_ign": ["Alice", "Bob"]}
    assert build.version_spec == "1.20"
    assert build.creators_ign == []
    await workspace.history.undo()
    assert workspace.patch().attributes() == {"version_spec": "1.21"}


async def test_an_invalid_value_stages_nothing_and_says_why() -> None:
    workspace = editor(_door()).workspace

    with sl.runtime.transaction():
        await workspace.save_values({"display_name": "x" * 500})

    assert workspace.patch().attributes() == {}
    assert "Too long" in sl.text.source_text(workspace.notice)


async def test_preview_includes_staged_values_without_changing_the_stored_build() -> None:
    build = _door(version_spec="1.20")
    screen = editor(build)
    assert screen.workspace.prefill({"version_compatibility": "1.21"}) is None
    screen.open_child = AsyncMock()  # type: ignore[method-assign]

    await screen._preview(cast(sd.PressEvent, press_event()))

    preview = cast(AsyncMock, screen._render_build).call_args.args[0]
    assert preview.version_spec == "1.21"
    assert preview is not build
    assert build.version_spec == "1.20"


async def test_review_lists_only_changed_fields_including_cleared_ones() -> None:
    screen = editor(_door(version_spec="1.20", creators_ign=["Alice"], completion_time="2024"))
    await screen.projection.reload()
    assert screen.workspace.prefill({"version_compatibility": None}) is None
    screen.confirming = True

    review = rendered(screen)

    assert "Works in versions: *Not set*" in review
    assert "Alice" not in review
    assert "2024" not in review


async def test_names_outside_the_catalogue_are_listed_and_can_only_be_removed() -> None:
    build = _door(component_restrictions=["Observerless"], miscellaneous_restrictions=["Hand-wired"])
    screen = editor(build)
    await screen.projection.reload()
    workspace = screen.workspace

    assert "Not in the catalogue: Hand-wired" in rendered(screen)
    # Kept names are written back, so an unrelated save never drops them.
    with sl.runtime.transaction():
        await workspace.save_values({"display_name": "Door"})
    assert workspace.patch().miscellaneous_restrictions is UNSET

    with sl.runtime.transaction():
        workspace.remove_unresolved({"Hand-wired"})

    assert workspace.unresolved == UnresolvedTaxonomy()
    assert workspace.patch().miscellaneous_restrictions == ["Observerless"]
    screen.confirming = True
    assert "Removed: Hand-wired" in rendered(screen)
