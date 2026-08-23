"""The owner-only mount inspector: what it lists, what it opens, and what it refuses."""

from datetime import UTC, datetime, timedelta
from typing import Any

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount, Owner, live
from squid_layouts.discord.devtools_view import MountInspector, metrics_text, plan_text, scene_attachment
from squid_layouts.discord.testing import assert_within_limits, commit_render, delivered_to, fake_interaction
from squid_layouts.primitives import Button, Heading, Row, Text


class Subject(sl.Component):
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


@pytest.fixture(autouse=True)
def _isolated_registry():
    live._LIVE.clear()
    yield
    live._LIVE.clear()


async def live_subject(**kwargs: Any) -> Mount:
    mount = Mount(Subject(), access=kwargs.pop("access", Everyone()), **kwargs)
    await mount.send(delivered_to(sl.discord.testing.fake_message(message_id=42)))
    return mount


def mount_inspector(inspector: MountInspector) -> tuple[Mount, discord.ui.LayoutView]:
    mount = Mount(inspector, access=Owner(1))
    return mount, commit_render(mount)


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class TestList:
    def test_the_inspector_authors_high_level_semantic_nodes(self) -> None:
        nodes = MountInspector().render()

        assert isinstance(nodes[0], sl.semantic.Section)
        assert isinstance(nodes[-1], sl.semantic.Actions)

    async def test_it_lists_a_live_mount_with_a_link_to_its_message(self) -> None:
        subject = await live_subject()
        _, view = mount_inspector(MountInspector())

        body = "\n".join(_texts(view))
        assert subject.id in body
        assert "Subject" in body
        assert "/42" in body

    def test_an_empty_process_says_so_rather_than_drawing_a_dead_picker(self) -> None:
        _, view = mount_inspector(MountInspector())

        assert "Nothing is mounted" in "\n".join(_texts(view))
        assert not [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]

    async def test_the_picker_opens_the_chosen_mount(self) -> None:
        subject = await live_subject()
        inspector = MountInspector()
        mount, _ = mount_inspector(inspector)

        await mount.dispatch("open", fake_interaction(), [subject.id])

        assert inspector.focus == subject.id
        assert f"Mount {subject.id}" in "\n".join(_texts(commit_render(mount)))

    async def test_it_marks_itself_in_its_own_list(self) -> None:
        inspector = MountInspector()
        mount, _ = mount_inspector(inspector)
        inspector.own_id = mount.id

        assert "(this panel)" in "\n".join(_texts(commit_render(mount)))


class TestDetail:
    async def test_a_detail_view_reports_state_plan_and_handlers(self) -> None:
        subject = await live_subject(access=Owner(7))
        await subject.refresh_now()
        _, view = mount_inspector(MountInspector(focus=subject.id))

        body = "\n".join(_texts(view))
        assert "'opened': False" in body
        assert "<@7>" in body
        assert "open" in body
        assert "states explored" in body
        assert "1 suppressed" in body

    async def test_a_detail_view_distinguishes_an_armed_dirty_application(self) -> None:
        now = datetime.now(UTC)
        reactor = sl.discord.Reactor(clock=lambda: now)
        interaction = fake_interaction(message_id=42)
        interaction.expires_at = now + timedelta(seconds=30)
        subject = Mount(
            Subject(),
            access=Everyone(),
            scheduler=reactor,
            timeout=None,
            expiry=sl.discord.RenewEphemeral(warning=60),
        )
        await subject.send(
            delivered_to(
                sl.discord.testing.fake_message(message_id=42, ephemeral=True),
                handle=sl.discord.delivery.handle_from(interaction),
            )
        )
        assert subject.handle is not None
        subject._queue_expiry_arm(subject.handle)
        await subject.refresh_now()
        subject.invalidate()

        _, view = mount_inspector(MountInspector(focus=subject.id))
        body = "\n".join(_texts(view))

        assert "renewal armed" in body
        assert "dirty" in body
        assert "edit handle 30s left" in body

    async def test_a_detail_view_reports_cell_versions_and_computed_sources(self) -> None:
        subject = await live_subject()
        await subject.dispatch("open", fake_interaction(message_id=42))
        _, view = mount_inspector(MountInspector(focus=subject.id))

        body = "\n".join(_texts(view))
        assert "opened v1" in body
        assert "label v2 <- $.opened" in body
        assert "unread (never evaluated)" in body

    async def test_it_reflects_the_subject_changing_under_it(self) -> None:
        subject = await live_subject()
        inspector = MountInspector(focus=subject.id)
        mount, _ = mount_inspector(inspector)
        await subject.dispatch("open", fake_interaction(message_id=42))

        # A handler that changes nothing leaves the mount clean, so Refresh has to be a
        # state change of its own or the message would keep showing the old dump.
        await mount.dispatch("refresh", fake_interaction())

        assert "'opened': True" in "\n".join(_texts(commit_render(mount)))

    async def test_a_mount_that_finished_while_the_panel_was_open_falls_back_to_the_list(self) -> None:
        subject = await live_subject()
        inspector = MountInspector(focus=subject.id)
        mount, _ = mount_inspector(inspector)
        await subject.finish(disable=False)

        await mount.dispatch("refresh", fake_interaction())
        body = "\n".join(_texts(commit_render(mount)))

        assert "no longer live" in body
        assert "Live mounts" in body

    async def test_back_returns_to_the_list(self) -> None:
        subject = await live_subject()
        inspector = MountInspector(focus=subject.id)
        mount, _ = mount_inspector(inspector)

        await mount.dispatch("back", fake_interaction())

        assert inspector.focus is None

    async def test_a_detail_view_fits_discord(self) -> None:
        subject = await live_subject()
        _, view = mount_inspector(MountInspector(focus=subject.id))

        assert_within_limits(view)

    async def test_a_registry_key_labels_its_mount(self) -> None:
        registry = sl.discord.SessionRegistry()
        subject = Mount(Subject(), access=Everyone())
        await registry.open(subject, delivered_to(sl.discord.testing.fake_message()), key=("editor", 7))

        _, view = mount_inspector(MountInspector(registry=registry))

        assert "('editor', 7)" in "\n".join(_texts(view))


class TestSceneDump:
    async def test_it_serializes_the_committed_scene(self) -> None:
        subject = await live_subject()

        asset = scene_attachment(subject.snapshot())

        assert asset is not None
        assert asset.name.endswith(".json")
        assert isinstance(asset.source, sl.semantic.InlineAsset)
        assert sl.scene.Codec.loads(asset.source.data.decode()) == subject.snapshot().scene

    def test_a_mount_with_no_committed_render_has_no_scene(self) -> None:
        assert scene_attachment(Mount(Subject(), access=Everyone()).snapshot()) is None


class TestPlanDiagnostics:
    async def test_plan_and_metrics_render_the_retained_result(self) -> None:
        subject = await live_subject()
        snapshot = subject.snapshot()

        assert "logical" in plan_text(snapshot)
        assert "states_explored:" in metrics_text(snapshot)
        assert "cache:" in metrics_text(snapshot)

    def test_uncommitted_mounts_explain_missing_diagnostics(self) -> None:
        snapshot = Mount(Subject(), access=Everyone()).snapshot()

        assert "no plan report" in plan_text(snapshot)
        assert "no planner metrics" in metrics_text(snapshot)
