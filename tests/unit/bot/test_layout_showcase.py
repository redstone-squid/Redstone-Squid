"""Public dogfood surface for the squid-layouts engine."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

import squid_discord as sd
import squid_layouts as sl
from squid.bot.layout_showcase import (
    Appearance,
    AppearancePanel,
    LayoutShowcase,
    LayoutShowcaseCog,
    Lobby,
    PreviewPanel,
    Session,
)
from squid_discord import Everyone, Mount, MountScheduler, Owner, SessionKey, SessionRegistry
from squid_discord.sessions import UserScope
from squid_discord.testing import (
    assert_within_limits,
    commit_render,
    delivered_to,
    fake_interaction,
    fake_message,
)
from tests.helpers.discord import make_layout_bot


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button[Any]]:
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


def test_pagination_exhibit_uses_the_measured_budget() -> None:
    mount = Mount(LayoutShowcase(section="pagination", entries=200, locale="en"), access=Everyone(), timeout=None)
    view = commit_render(mount)

    assert "#011" in _texts(view)
    assert "#200" not in _texts(view)
    assert any(button.label == "Next" for button in _buttons(view))
    assert_within_limits(view)


def test_structural_exhibit_folds_the_oversized_action_surface() -> None:
    view = commit_render(
        Mount(LayoutShowcase(section="adaptation", entries=20, locale="en"), access=Everyone(), timeout=None)
    )

    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]
    assert [
        len(select.options) for select in selects if select.custom_id and "showcase-actions" in select.custom_id
    ] == [
        25,
        11,
    ]
    assert not any(button.label == "Action 36" for button in _buttons(view))
    assert_within_limits(view)


@pytest.mark.parametrize(
    ("section", "source_marker"),
    [
        ("pagination", "sl.primitives.Paginate("),
        ("adaptation", 'return sl.actions(*actions, key="showcase-actions")'),
        ("degradation", "overflow=sl.primitives.Spill()"),
        ("data", 'sl.table(columns, *rows, key="capability-table")'),
        ("grid", 'return sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid)'),
        ("ownership", "on=sl.controlled(self.subscribed, self._set_subscribed)"),
        ("forms", "class FeedbackForm(sl.forms.Form)"),
        ("composition", 'self.boundary(self.left, key="left")'),
        ("localization", 'mount.localize(localization_for("zh-CN"))'),
        ("history", "case sl.runtime.HistoryResultStatus.CONFLICT:"),
        ("replication", 'document.counter("votes").increment(2)'),
        ("effects", '@sl.operation(initial="queued")'),
    ],
)
def test_each_exhibit_shows_its_author_facing_declaration(section: str, source_marker: str) -> None:
    view = commit_render(
        Mount(
            LayoutShowcase(section=section, entries=20, locale="en"),  # type: ignore[arg-type]
            access=Everyone(),
            timeout=None,
        )
    )
    content = _texts(view)

    assert "Declaration source" in content
    assert source_marker in content
    assert_within_limits(view)


def test_degradation_exhibit_makes_each_compromise_visible() -> None:
    mount = Mount(LayoutShowcase(section="degradation", entries=20, locale="en"), access=Everyone(), timeout=None)
    view = commit_render(mount)

    assert "…and 15 more" in _texts(view)
    assert "The report records every compromise" in _texts(view)
    assert mount.plan is not None
    assert len(mount.plan.report.events) >= 2
    assert_within_limits(view)


def test_data_exhibit_formats_typed_nodes_rather_than_strings() -> None:
    mount = Mount(LayoutShowcase(section="data", entries=40, locale="en"), access=Everyone(), timeout=None)
    view = commit_render(mount)
    content = _texts(view)

    assert "**Loaded samples:** 40 rows" in content
    assert "░░░░░░░░░░ 0%" in content, "a proportion, drawn from the value and its maximum"
    assert "<t:" in content, "the instant reaches each reader in their own timezone"
    assert ":R>" in content, "and does so relative to when they read it"
    assert "Adapts by" in content, "the table kept its tabular shape"
    assert "Pickers of 25 and 11" in content, "with every declared row"
    assert_within_limits(view)


async def test_grid_exhibit_keeps_spatial_rows_and_stable_selection_keys() -> None:
    component = LayoutShowcase(section="grid", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    view = commit_render(mount)
    numbered = [button for button in _buttons(view) if button.label and button.label.isdecimal()]

    assert [button.label for button in numbered] == [str(index) for index in range(1, 13)]
    assert numbered[5].disabled
    assert numbered[10].disabled

    await mount.dispatch("showcase-grid.cell-0", fake_interaction())

    assert component.grid_pick == "Selected cell-0."


async def test_ownership_exhibit_separates_session_owned_and_component_owned_values() -> None:
    component = LayoutShowcase(section="ownership", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("ownership.controlled", fake_interaction())
    await mount.dispatch("ownership.rating.4", fake_interaction())

    assert component.subscribed is True, "a controlled value only moves through its handler"
    assert component.rating == 4
    assert "\N{BLACK STAR}" * 4 in _texts(commit_render(mount))

    await mount.dispatch("ownership.managed", fake_interaction())
    labels = [button.label for button in _buttons(commit_render(mount))]

    assert "Session-owned toggle: Session says on" in labels
    assert mount.presentation.toggles["ownership.managed"].on is True, "the session holds it, not the component"
    assert not hasattr(component, "managed"), "no component state backs the managed toggle"


async def test_forms_exhibit_validates_then_binds_typed_values_and_prefills() -> None:
    component = LayoutShowcase(section="forms", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)
    assert mount.plan is not None
    binding = mount.plan.form_bindings["feedback"]

    rejected = await binding.spec.evaluate({"exhibit": "data", "headline": "Readable", "score": "1"})
    caught = [issue.key for issue in rejected.errors if isinstance(issue, sl.forms.FieldError)]
    assert caught == ["detail"], "cross-field validation sees typed values"

    await mount.dispatch_submit(
        "feedback",
        fake_interaction(),
        binding.spec,
        {"exhibit": "data", "headline": "Typed all the way down", "score": "5"},
        binding.on_submit,
    )

    assert (component.feedback_exhibit, component.feedback_headline, component.feedback_score) == (
        "data",
        "Typed all the way down",
        5,
    )
    view = commit_render(mount)
    assert "Typed all the way down" in _texts(view)
    assert mount.plan.form_bindings["feedback"].spec.prefill["headline"] == "Typed all the way down"
    assert_within_limits(view)


async def test_localization_exhibit_escapes_values_and_relocalizes_the_same_mount() -> None:
    component = LayoutShowcase(section="localization", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    first = commit_render(mount)

    assert "\\*operator input\\*" in _texts(first)
    assert "@\u200beveryone" in _texts(first)

    interaction = fake_interaction()
    await mount.dispatch("switch-language", interaction)

    assert component.display_locale == "zh-CN"
    assert mount.localization.locale == "zh-CN"
    assert interaction.response.edit_message.await_count == 1
    edited_view = interaction.response.edit_message.await_args.kwargs["view"]
    assert "延迟本地化与安全 Markdown" in _texts(edited_view)


async def test_composed_children_keep_independent_state_and_keys() -> None:
    component = LayoutShowcase(section="composition", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    view = commit_render(mount)
    ids = {button.custom_id or "" for button in _buttons(view)}

    assert any("left.increment" in custom_id for custom_id in ids)
    assert any("right.increment" in custom_id for custom_id in ids)

    await mount.dispatch("left.increment", fake_interaction())

    assert component.left.count == 1
    assert component.right.count == 0


async def test_history_exhibit_preserves_a_sibling_write_and_presents_rollback_aftermath() -> None:
    component = LayoutShowcase(section="history", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("history.rename", fake_interaction())
    assert component.project_name == "Action Ledger"
    assert component.outcome_result.startswith("COMMITTED · local sequence")

    await mount.dispatch("history.sibling", fake_interaction())
    await mount.dispatch("history.undo", fake_interaction())

    assert component.project_name == "Squid after a sibling edit"
    assert component.history_result.startswith("Undo: CONFLICT; no state changed")
    assert component.action_history.entries[0].state is sl.runtime.HistoryEntryState.CONFLICTED

    await mount.dispatch("history.rollback", fake_interaction())

    assert component.project_name == "Squid after a sibling edit", "the staged rollback value never published"
    assert component.outcome_result.startswith("ROLLED BACK · handler_exception")
    assert component.outcome_result.endswith("recovery is a fresh action")

    await mount.dispatch("history.drop", fake_interaction())
    assert component.action_history.entries == ()
    assert component.project_name == "Squid after a sibling edit", "dropping history is not a forced restore"


async def test_replication_exhibit_selectively_undoes_only_the_local_contribution() -> None:
    component = LayoutShowcase(section="replication", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("replication.local", fake_interaction())
    assert component.local_document.counter("votes").value == 2
    assert component.local_document.set("reviewers").value == frozenset({"mine"})

    await mount.dispatch("replication.peer", fake_interaction())
    assert component.local_document.counter("votes").value == 5
    assert component.local_document.set("reviewers").value == frozenset({"mine", "peer"})
    assert component.peer_document.snapshot() == component.local_document.snapshot()

    await mount.dispatch("replication.undo", fake_interaction())

    assert component.local_document.counter("votes").value == 3
    assert component.local_document.set("reviewers").value == frozenset({"peer"})
    assert component.replication_result.startswith("Selective undo: APPLIED as action")


async def test_effects_exhibit_retries_compensation_and_accepts_an_operation_result() -> None:
    component = LayoutShowcase(section="effects", entries=20, locale="en")
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("effects.publish", fake_interaction())
    first_execution = component.publication
    assert first_execution is not None
    assert isinstance(first_execution.status, sl.operations.Succeeded)

    await mount.dispatch("effects.accept", fake_interaction())
    assert component.published_revision == 41

    await mount.dispatch("effects.publish", fake_interaction())
    assert component.publication is not None
    assert component.publication.context.execution_id != first_execution.context.execution_id

    await mount.dispatch("effects.create", fake_interaction())
    await mount.dispatch("effects.fail", fake_interaction())
    await mount.dispatch("effects.undo", fake_interaction())

    assert component.channel_service.exists is True
    assert component.channel_present is True
    assert component.compensation_result.startswith("Compensation: FAILED")

    await mount.dispatch("effects.undo", fake_interaction())

    assert component.channel_service.exists is False
    assert component.channel_present is False
    assert component.compensation_result.startswith("Compensation: APPLIED as action")


async def test_demo_command_and_controls_are_public() -> None:
    settings = SimpleNamespace(get_locale=AsyncMock(return_value=None))
    bot = make_layout_bot(services=SimpleNamespace(settings=settings), topic_bus=sl.runtime.LocalTopicBus())
    cog = LayoutShowcaseCog(cast(Any, bot))
    ctx = cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                bot=bot,
                interaction=None,
                guild=None,
                author=SimpleNamespace(id=7),
                send=AsyncMock(return_value=fake_message(message_id=1)),
            ),
        ),
    )

    await LayoutShowcaseCog.demo.callback(cog, ctx, "pagination", 20)  # type: ignore[arg-type]

    sent = cast(Any, ctx).send.await_args.kwargs
    assert sent["ephemeral"] is False
    assert isinstance(sent["view"], discord.ui.LayoutView)
    demo = next(command for command in cog.__cog_commands__ if command.qualified_name == "layout demo")
    assert demo.checks == []


class TestSharedAppearance:
    """The worked example: two live panels agreeing on view state neither of them owns."""

    def panels(self) -> tuple[sl.runtime.LocalTopicBus, MountScheduler, Appearance, Session, Mount, Mount]:
        bus = sl.runtime.LocalTopicBus()
        scheduler = MountScheduler(bus)
        scope = UserScope(7)
        appearance, session = Appearance(bus, scope), Session(bus, scope)
        writer = Mount(AppearancePanel(appearance, session), access=Owner(7), scheduler=scheduler, timeout=None)
        reader = Mount(PreviewPanel(appearance, session), access=Owner(7), scheduler=scheduler, timeout=None)
        return bus, scheduler, appearance, session, writer, reader

    async def test_the_reading_panel_follows_what_it_rendered(self) -> None:
        _, _, appearance, session, writer, reader = self.panels()
        await reader.send(delivered_to(fake_message(message_id=2)))

        assert set(reader.followed) == {
            sl.runtime.CellAddress(appearance, "accent"),
            sl.runtime.CellAddress(appearance, "density"),
            sl.runtime.CellAddress(session, "focus"),
        }
        assert writer.followed == ()

    async def test_a_press_on_one_panel_schedules_the_other(self) -> None:
        bus, scheduler, _, _, writer, reader = self.panels()
        await writer.send(delivered_to(fake_message(message_id=1)))
        await reader.send(delivered_to(fake_message(message_id=2)))

        await writer.dispatch("controls.density", fake_interaction(user_id=7))

        assert reader in scheduler._queued

    async def test_the_injected_namespace_reaches_a_leaf_and_undo_covers_it(self) -> None:
        _, _, appearance, _, writer, _ = self.panels()
        await writer.send(delivered_to(fake_message(message_id=1)))

        await writer.dispatch("controls.density", fake_interaction(user_id=7))
        assert appearance.density == "compact"

        await writer.dispatch("controls.undo", fake_interaction(user_id=7))
        assert appearance.density == "comfortable", "one entry restores state the panel does not own"

    async def test_the_accent_button_declares_its_own_recording(self) -> None:
        """`record=` on the control: the handler only writes, the framework opens the entry."""
        _, _, appearance, _, writer, _ = self.panels()
        await writer.send(delivered_to(fake_message(message_id=1)))
        before = appearance.accent

        await writer.dispatch("controls.accent", fake_interaction(user_id=7))
        assert appearance.accent != before

        await writer.dispatch("controls.undo", fake_interaction(user_id=7))
        assert appearance.accent == before

    async def test_the_session_dies_with_its_panels_and_appearance_does_not(self) -> None:
        import gc
        import weakref

        _, _, appearance, session, writer, reader = self.panels()
        await writer.send(delivered_to(fake_message(message_id=1)))
        await reader.send(delivered_to(fake_message(message_id=2)))
        gone, kept = weakref.ref(session), weakref.ref(appearance)

        await writer.finish(disable=False)
        await reader.finish(disable=False)
        del session, writer, reader
        gc.collect()

        assert gone() is None, "co-existence state: nothing was looking at it"
        assert kept() is appearance, "retention state: the caller still holds it"


def _lobby_mount(panel: Lobby) -> Mount:
    assert panel._mount is not None
    return panel._mount


def _lobby_session(panel: Lobby) -> sd.sessions.Session:
    session = panel._session()
    assert session is not None
    return session


class TestLobby:
    """The roster is session membership, so the panel holds none of it."""

    async def opened(
        self,
        *,
        capacity: int = 4,
        registry: SessionRegistry | None = None,
        guild_id: int = 5,
        host_id: int = 7,
    ) -> tuple[SessionRegistry, Lobby]:
        bot = make_layout_bot()
        registry = bot.mounts if registry is None else registry
        panel = Lobby(registry, host_id=host_id)
        result = await registry.open(
            panel.mount(source=bot),
            delivered_to(fake_message(message_id=1)),
            key=SessionKey.guild("showcase-lobby", guild_id),
            actor_id=host_id,
            capacity=capacity,
            quota=1,
        )
        assert isinstance(result, sd.sessions.Opened)
        return registry, panel

    async def test_the_host_opens_as_the_only_member(self) -> None:
        registry, panel = await self.opened()

        assert next(iter(registry.active())).members == frozenset({7})
        assert "### Players — 1/4" in _texts(commit_render(_lobby_mount(panel)))

    async def test_a_press_from_anyone_joins_and_the_roster_redraws(self) -> None:
        registry, panel = await self.opened()

        await _lobby_mount(panel).dispatch("lobby-roster.players", fake_interaction(user_id=8))

        assert next(iter(registry.active())).members == frozenset({7, 8})
        assert "### Players — 2/4" in _texts(commit_render(_lobby_mount(panel)))

    async def test_the_lobby_fills_and_then_refuses(self) -> None:
        registry, panel = await self.opened(capacity=2)

        await _lobby_mount(panel).dispatch("lobby-roster.players", fake_interaction(user_id=8))
        await _lobby_mount(panel).dispatch("lobby-roster.players", fake_interaction(user_id=9))

        assert next(iter(registry.active())).members == frozenset({7, 8})

    async def test_a_roster_dependent_rule_closes_the_lobby_when_the_host_leaves(self) -> None:
        registry, panel = await self.opened()
        session = next(iter(registry.active()))

        await _lobby_mount(panel).dispatch("leave", fake_interaction(user_id=7))
        await _lobby_mount(panel).dispatch("lobby-roster.players", fake_interaction(user_id=8))

        assert session.members == frozenset()
        assert not session.root.finished

    async def test_only_a_member_may_start(self) -> None:
        """Everyone may press Join, so the control that is not for everyone checks itself."""
        _, panel = await self.opened()

        await _lobby_mount(panel).dispatch("start", fake_interaction(user_id=8))
        assert panel.started_with is None

        await _lobby_mount(panel).dispatch("lobby-roster.players", fake_interaction(user_id=8))
        await _lobby_mount(panel).dispatch("start", fake_interaction(user_id=8))

        assert panel.started_with == 2
        assert "Started with 2 players." in _texts(commit_render(_lobby_mount(panel)))

    async def test_a_reader_cannot_hold_a_seat_in_two_servers(self) -> None:
        """Two lobbies, two hosts, one reader: the quota is what stops the second seat."""
        registry, here = await self.opened()
        _, elsewhere = await self.opened(registry=registry, guild_id=6, host_id=9)

        await _lobby_mount(elsewhere).dispatch("lobby-roster.players", fake_interaction(user_id=8))
        await _lobby_mount(here).dispatch("lobby-roster.players", fake_interaction(user_id=8))

        assert registry.sessions_for_member(8) == (registry.sessions_for_member(8)[0],)
        assert 8 in _lobby_session(elsewhere).members
        assert 8 not in _lobby_session(here).members
