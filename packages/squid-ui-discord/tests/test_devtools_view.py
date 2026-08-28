"""The owner-only mount inspector: what it lists, what it opens, and what it refuses."""

from datetime import UTC, datetime, timedelta
from typing import Any

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui.primitives import Button, Heading, Row, Text
from squid_ui_discord import Everyone, MessageRoot, Owner
from squid_ui_discord import testing as sd
from squid_ui_discord.devtools_runtime import DevToolsRuntime
from squid_ui_discord.devtools_view import (
    MessageRootInspector,
    OperationalInspector,
    metrics_text,
    plan_text,
    scene_attachment,
)
from squid_ui_discord.testing import assert_within_limits, commit_render, delivered_to, fake_interaction


class Subject(sl.Component[sl.ComponentsV2Target]):
    """A stand-in for whatever panel the owner is actually debugging."""

    opened: bool = sl.state(default=False)

    @sl.computed
    def label(self) -> str:
        return "open" if self.opened else "closed"

    @sl.computed
    def unread(self) -> str:
        """Never rendered, so never evaluated -- which the inspector should say."""
        return "unused"

    def render(self):
        return [
            Heading("Subject"),
            Text(f"opened: {self.opened} ({self.label})"),
            Row((Button(label="Open", on_click=self._open, key="open"),)),
        ]

    async def _open(self, event: sl.PressEvent) -> None:
        self.opened = True


async def live_subject(**kwargs: Any) -> MessageRoot:
    message_root = MessageRoot(Subject(), access=kwargs.pop("access", Everyone()), **kwargs)
    await message_root.send(delivered_to(squid_ui_discord.testing.fake_message(message_id=42)))
    return message_root


def message_root_inspector(inspector: MessageRootInspector) -> tuple[MessageRoot, discord.ui.LayoutView]:
    message_root = MessageRoot(inspector, access=Owner(1))
    return message_root, commit_render(message_root)


class TestList:
    def test_the_inspector_authors_high_level_semantic_nodes(self) -> None:
        nodes = MessageRootInspector().render()

        assert isinstance(nodes[0], sl.semantic.Section)
        assert isinstance(nodes[-1], sl.semantic.ActionControls)

    async def test_it_lists_a_live_message_root_with_a_link_to_its_message(self) -> None:
        subject = await live_subject()
        _, view = message_root_inspector(MessageRootInspector())

        body = "\n".join(sd.payload_texts(view))
        assert subject.id in body
        assert "Subject" in body
        assert "/42" in body

    def test_an_empty_process_says_so_rather_than_drawing_a_dead_picker(self) -> None:
        _, view = message_root_inspector(MessageRootInspector())

        assert "Nothing is mounted" in "\n".join(sd.payload_texts(view))
        assert not [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]

    async def test_the_picker_opens_the_chosen_root(self) -> None:
        subject = await live_subject()
        inspector = MessageRootInspector()
        message_root, _ = message_root_inspector(inspector)

        await message_root.dispatch("open", fake_interaction(), [subject.id])

        assert inspector.focus == subject.id
        assert f"MessageRoot {subject.id}" in "\n".join(sd.payload_texts(commit_render(message_root)))

    async def test_it_marks_itself_in_its_own_list(self) -> None:
        inspector = MessageRootInspector()
        message_root, _ = message_root_inspector(inspector)
        inspector.own_id = message_root.id

        assert "(this panel)" in "\n".join(sd.payload_texts(commit_render(message_root)))


class TestDetail:
    async def test_a_detail_view_reports_state_plan_and_handlers(self) -> None:
        subject = await live_subject(access=Owner(7))
        await subject.refresh()
        _, view = message_root_inspector(MessageRootInspector(focus=subject.id))

        body = "\n".join(sd.payload_texts(view))
        assert "'opened': False" in body
        assert "<@7>" in body
        assert "open" in body
        assert "states explored" in body
        assert "1 suppressed" in body

    async def test_a_detail_view_distinguishes_an_armed_dirty_application(self) -> None:
        now = datetime.now(UTC)
        scheduler = squid_ui_discord.MessageRootScheduler(clock=lambda: now)
        interaction = fake_interaction(message_id=42)
        interaction.expires_at = now + timedelta(seconds=30)
        subject = MessageRoot(
            Subject(),
            access=Everyone(),
            scheduler=scheduler,
            timeout=None,
            expiry=squid_ui_discord.RenewEphemeral(warning=60),
        )
        await subject.send(
            delivered_to(
                squid_ui_discord.testing.fake_message(message_id=42, ephemeral=True),
                handle=squid_ui_discord.delivery.handle_from(interaction),
            )
        )
        assert subject.handle is not None
        subject._queue_expiry_arm(subject.handle)
        await subject.refresh()
        subject.invalidate()

        _, view = message_root_inspector(MessageRootInspector(focus=subject.id))
        body = "\n".join(sd.payload_texts(view))

        assert "renewal armed" in body
        assert "dirty" in body
        assert "edit handle 30s left" in body

    async def test_a_detail_view_reports_cell_versions_and_computed_sources(self) -> None:
        subject = await live_subject()
        await subject.dispatch("open", fake_interaction(message_id=42))
        _, view = message_root_inspector(MessageRootInspector(focus=subject.id))

        body = "\n".join(sd.payload_texts(view))
        assert "opened v1" in body
        assert "label v2 <- $.opened" in body
        assert "unread (never evaluated)" in body

    async def test_it_reflects_the_subject_changing_under_it(self) -> None:
        subject = await live_subject()
        inspector = MessageRootInspector(focus=subject.id)
        message_root, _ = message_root_inspector(inspector)
        await subject.dispatch("open", fake_interaction(message_id=42))

        # A handler that changes nothing leaves the mount clean, so Refresh has to be a
        # state change of its own or the message would keep showing the old dump.
        await message_root.dispatch("refresh", fake_interaction())

        assert "'opened': True" in "\n".join(sd.payload_texts(commit_render(message_root)))

    async def test_a_message_root_that_finished_while_the_panel_was_open_falls_back_to_the_list(self) -> None:
        subject = await live_subject()
        inspector = MessageRootInspector(focus=subject.id)
        message_root, _ = message_root_inspector(inspector)
        await subject.finish(disable=False)

        await message_root.dispatch("refresh", fake_interaction())
        body = "\n".join(sd.payload_texts(commit_render(message_root)))

        assert "no longer live" in body
        assert "Message roots" in body

    async def test_back_returns_to_the_list(self) -> None:
        subject = await live_subject()
        inspector = MessageRootInspector(focus=subject.id)
        message_root, _ = message_root_inspector(inspector)

        await message_root.dispatch("back", fake_interaction())

        assert inspector.focus is None

    async def test_a_detail_view_fits_discord(self) -> None:
        subject = await live_subject()
        _, view = message_root_inspector(MessageRootInspector(focus=subject.id))

        assert_within_limits(view)

    async def test_a_manager_key_labels_its_root(self) -> None:
        manager = squid_ui_discord.SessionManager()
        subject = MessageRoot(Subject(), access=Everyone())
        await manager.open(subject, delivered_to(squid_ui_discord.testing.fake_message()), key=("editor", 7))

        _, view = message_root_inspector(MessageRootInspector(manager=manager))

        assert "('editor', 7)" in "\n".join(sd.payload_texts(view))


class TestSceneDump:
    async def test_it_serializes_the_committed_scene(self) -> None:
        subject = await live_subject()

        asset = scene_attachment(subject.snapshot())

        assert asset is not None
        assert asset.name.endswith(".json")
        assert isinstance(asset.source, sl.document.InlineAsset)
        assert sl.scene.Codec.loads(asset.source.data.decode()) == subject.snapshot().scene

    def test_a_message_root_with_no_committed_render_has_no_scene(self) -> None:
        assert scene_attachment(MessageRoot(Subject(), access=Everyone()).snapshot()) is None


class TestPlanDiagnostics:
    async def test_plan_and_metrics_render_the_retained_result(self) -> None:
        subject = await live_subject()
        snapshot = subject.snapshot()

        assert "logical" in plan_text(snapshot)
        assert "states_explored:" in metrics_text(snapshot)
        assert "cache:" in metrics_text(snapshot)

    def test_uncommitted_mounts_explain_missing_diagnostics(self) -> None:
        snapshot = MessageRoot(Subject(), access=Everyone()).snapshot()

        assert "no plan report" in plan_text(snapshot)
        assert "no planner metrics" in metrics_text(snapshot)


class TestOperationalInspectorSections:
    """Every section renders.

    These panels only run when a developer opens that tab, so a name that does not exist or a
    required argument that is not passed sits undetected until someone reaches for it mid-debug.
    """

    @pytest.mark.parametrize(
        "section",
        ["overview", "roots", "sessions", "queues", "profile", "persistence"],
    )
    async def test_each_dashboard_section_renders(self, section: str) -> None:
        inspector = OperationalInspector(DevToolsRuntime())
        inspector.section = section

        assert list(inspector.render())

    async def test_root_and_session_detail_panels_render(self) -> None:
        subject = await live_subject()
        runtime = DevToolsRuntime()
        inspector = OperationalInspector(runtime)

        inspector.section = "roots"
        inspector.message_root_id = subject.id
        assert list(inspector.render())

        snapshot = runtime.snapshot()
        if snapshot.sessions:
            inspector.section = "sessions"
            inspector.session_id = snapshot.sessions[0].id
            assert list(inspector.render())
