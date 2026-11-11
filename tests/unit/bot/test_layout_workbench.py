"""The build workbench demonstrates actual framework behavior across message roots."""

from dataclasses import replace

import anyio
import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.layout_workbench import BuildDraft, BuildForm, BuildPreview, BuildSample, Workbench
from squid_ui_discord import MessageRoot, MessageRootScheduler, Owner
from squid_ui_discord.testing import (
    assert_within_limits,
    commit_render,
    delivered_to,
    interaction_harness,
    message_harness,
)


def workbench() -> tuple[BuildDraft, Workbench, MessageRoot, MessageRoot, MessageRootScheduler]:
    bus = sl.runtime.LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    draft = BuildDraft()
    editor = Workbench(draft)
    return (
        draft,
        editor,
        MessageRoot(editor, access=Owner(7), scheduler=scheduler, timeout=None),
        MessageRoot(BuildPreview(draft), access=Owner(7), scheduler=scheduler, timeout=None),
        scheduler,
    )


async def test_presets_update_other_message_and_undo() -> None:
    draft, _, writer, reader, scheduler = workbench()
    await writer.send(delivered_to(message_harness(message_id=1)))
    await reader.send(delivered_to(message_harness(message_id=2)))
    await writer.dispatch("large", interaction_harness(user_id=7))
    assert draft.build.oversized
    assert reader in scheduler._queued
    assert_within_limits(commit_render(reader))
    assert reader.plan is not None
    assert reader.plan.report.events
    await writer.dispatch("undo", interaction_harness(user_id=7))
    assert draft.build == BuildSample()
    await writer.dispatch("redo", interaction_harness(user_id=7))
    assert draft.build.oversized


@pytest.mark.parametrize("oversized", [False, True])
def test_preview_and_editor_fit(oversized: bool) -> None:
    draft, _, writer, reader, _ = workbench()
    draft.build = replace(draft.build, oversized=oversized)
    assert_within_limits(commit_render(writer))
    assert_within_limits(commit_render(reader))


def test_form_validates_dimensions_without_losing_values() -> None:
    draft = BuildDraft()
    editor = Workbench(draft)
    form = BuildForm(draft, editor.history)
    form.dimensions = "0x4x3"
    assert tuple(form.validate())
    assert form.prefill["name"] == draft.build.title
    form.dimensions = "4 x 4 x 3"
    assert not tuple(form.validate())


async def test_publish_captures_revision_and_rejects_duplicate_start() -> None:
    draft, _, writer, reader, _ = workbench()
    await writer.send(delivered_to(message_harness(message_id=1)))
    await reader.send(delivered_to(message_harness(message_id=2)))
    generation = writer.generation
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(writer.dispatch, "publish", interaction_harness(user_id=7))
        with anyio.fail_after(3):
            while draft.publication is None:
                await anyio.sleep(0.01)
        execution = draft.publication
        assert draft.publishing
        tasks.start_soon(reader.dispatch, "tag-2", interaction_harness(user_id=7))
        with anyio.fail_after(3):
            while draft.build.tag == "Flush":
                await anyio.sleep(0.01)
        assert draft.publishing
        await writer.dispatch("publish", interaction_harness(user_id=7), generation=generation)
        assert draft.publication is execution
        await writer.dispatch("large", interaction_harness(user_id=7))
    assert await execution == BuildSample()
    assert draft.publication_text() == "Draft changed · publish again"


async def test_failed_publication_can_retry() -> None:
    draft, editor, writer, _, _ = workbench()
    await writer.send(delivered_to(message_harness()))
    await writer.dispatch("fail", interaction_harness(user_id=7))
    await writer.dispatch("publish", interaction_harness(user_id=7))
    execution = draft.publication
    assert execution is not None
    with pytest.raises(RuntimeError, match="Simulated connection failure"):
        await execution
    assert not editor.fail_next
    assert not draft.publishing
    assert "retry" in draft.publication_text()
    await writer.dispatch("publish", interaction_harness(user_id=7))
    retry = draft.publication
    assert retry is not None
    assert retry is not execution
    await retry
    assert draft.publication_text() == "Published · simulated"


async def test_inspector_captures_committed_report_and_refreshes() -> None:
    draft = BuildDraft()
    editor = Workbench(draft)
    draft.build = replace(draft.build, oversized=True)
    preview = BuildPreview(draft)
    async with sd.testing.stage(preview, owner=object()) as ui:
        assert ui.root.plan is not None
        codes = [event.code for event in ui.root.plan.report.events]
        await ui.press("inspect")
        assert "oversized" in preview.inspection
        assert any(code in preview.inspection for code in codes)
        assert_within_limits(commit_render(ui.root))
    async with sd.testing.stage(editor, owner=object()) as ui:
        await ui.press("small")
        await ui.press("inspect")
        assert "undo=True" in editor.inspection
        assert_within_limits(commit_render(ui.root))
        await ui.press("hide-inspector")
        assert editor.inspection == ""


async def test_form_submission_is_one_undoable_edit() -> None:
    draft = BuildDraft()
    editor = Workbench(draft)
    async with sd.testing.stage(editor, owner=object()) as ui:
        form = await ui.press_for_form("edit")
        form.assert_within_limits()
        await form.submit(
            {"name": "Vault door", "dimensions": "5x5x4", "ticks": "24", "description": "A wider vault door."}
        )
        assert draft.build.title == "Vault door"
        assert draft.build.ticks == 24
        await ui.press("undo")
        assert draft.build == BuildSample()
        await ui.press("redo")
        assert draft.build.dimensions == "5x5x4"


async def test_invalid_form_keeps_draft_and_offers_prefilled_retry() -> None:
    draft = BuildDraft()
    editor = Workbench(draft)
    async with sd.testing.stage(editor, owner=object()) as ui:
        form = await ui.press_for_form("edit")
        attempt = await form.submit(
            {"name": "Vault door", "dimensions": "0x4x3", "ticks": "24", "description": "A wider vault door."}
        )
        assert draft.build == BuildSample()
        assert not editor.history.can_undo
        view = attempt.response.send_message.await_args.kwargs["view"]
        retry = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))
        opening = interaction_harness()
        await retry.callback(opening)
        modal = opening.response.send_modal.await_args.args[0]
        values = {
            item.custom_id: item.default for item in modal.walk_children() if isinstance(item, discord.ui.TextInput)
        }
        assert values["dimensions"] == "0x4x3"
        assert values["name"] == "Vault door"
