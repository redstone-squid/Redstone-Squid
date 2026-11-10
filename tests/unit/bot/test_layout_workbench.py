"""The build workbench demonstrates actual framework behavior across message roots."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid.bot.layout_workbench import BuildDraft, BuildForm, BuildPreview, BuildSample, Workbench
from squid_ui_discord import MessageRoot, MessageRootScheduler, Owner
from squid_ui_discord.sessions import UserScope
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
    draft = BuildDraft(bus, UserScope(7))
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
    draft, _, _, _, _ = workbench()
    form = BuildForm(draft)
    form.dimensions = "0x4x3"
    assert tuple(form.validate())
    assert form.prefill["name"] == draft.build.title
    form.dimensions = "4 x 4 x 3"
    assert not tuple(form.validate())
