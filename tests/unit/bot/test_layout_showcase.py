"""Public dogfood surface for the squid-ui engine."""

from dataclasses import dataclass
from typing import Any, cast

import discord
import pytest
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.layout_showcase import (
    Appearance,
    AppearancePanel,
    LayoutShowcase,
    LayoutShowcaseCog,
    Lobby,
    PreviewPanel,
    Session,
)
from squid.settings.application import SettingsService
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler, Owner
from squid_ui_discord.sessions import UserScope
from squid_ui_discord.testing import (
    ContextHarness,
    MessageHarness,
    assert_within_limits,
    commit_render,
    delivered_to,
    interaction_harness,
    message_harness,
)
from tests.support.discord import make_layout_bot


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


@dataclass(frozen=True)
class Services:
    settings: SettingsService


@dataclass(frozen=True)
class Guild:
    id: int


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button[Any]]:
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


def test_pagination_exhibit_pages_a_list_nobody_measured_by_hand() -> None:
    """A page a reader can take in, and a footer counted as part of it."""
    message_root = MessageRoot(
        LayoutShowcase(section="pagination", entries=200, locale="en"), access=Everyone(), timeout=None
    )
    view = commit_render(message_root)

    assert "#006" in _texts(view), "the asked-for page size"
    assert "#007" not in _texts(view), "and not one entry more"
    assert "Page 1 of 34 \N{MIDDLE DOT} 200 builds in total" in _texts(view)
    assert any(button.label == "Next" for button in _buttons(view))
    assert_within_limits(view)


def test_structural_exhibit_folds_the_oversized_action_surface() -> None:
    view = commit_render(
        MessageRoot(LayoutShowcase(section="adaptation", entries=20, locale="en"), access=Everyone(), timeout=None)
    )

    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]
    assert [
        len(select.options) for select in selects if select.custom_id and "showcase-actions" in select.custom_id
    ] == [
        25,
        11,
    ]
    assert not any(button.label == "Option 36" for button in _buttons(view))
    assert_within_limits(view)


@pytest.mark.parametrize(
    ("section", "source_marker"),
    [
        ("pagination", "sl.primitives.Paginate("),
        ("adaptation", 'return sl.action_controls(*choices, key="showcase-actions")'),
        ("degradation", "sl.budget(sl.spill("),
        ("data", 'sl.table(columns, *rows, key="builds-table")'),
        ("grid", 'return sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid)'),
        ("ownership", "on=sl.controlled(self.subscribed, self._set_subscribed)"),
        ("forms", "class FeedbackForm(sl.forms.Form)"),
        ("composition", 'self.boundary(self.left, key="left")'),
        ("localization", 'mount.localize(localization_for("zh-CN"))'),
        ("history", "case sl.runtime.HistoryResultStatus.CONFLICT:"),
        ("replication", 'document.set("reviewers").add("you")'),
        ("effects", '@sl.operation(initial="queued")'),
    ],
)
async def test_each_exhibit_keeps_its_declaration_one_press_away(section: str, source_marker: str) -> None:
    """Collapsed, the listing costs a button; a reader who wants it presses once."""
    message_root = MessageRoot(
        LayoutShowcase(section=section, entries=20, locale="en"),  # type: ignore[arg-type]
        access=Everyone(),
        timeout=None,
    )
    collapsed = commit_render(message_root)

    assert source_marker not in _texts(collapsed), "the listing is not in the message a reader is sent"
    assert any(button.label == "Show the code behind this exhibit" for button in _buttons(collapsed))

    await message_root.dispatch("source.toggle", interaction_harness())
    expanded = commit_render(message_root)

    assert source_marker in _texts(expanded)
    assert_within_limits(expanded)


def test_degradation_exhibit_makes_each_compromise_visible() -> None:
    message_root = MessageRoot(
        LayoutShowcase(section="degradation", entries=20, locale="en"), access=Everyone(), timeout=None
    )
    view = commit_render(message_root)

    assert "…and 11 more" in _texts(view), "whole log lines go, and it says how many"
    assert "Nothing here was shortened without telling you." in _texts(view), "the promise is never the cut"
    assert message_root.plan is not None
    assert len(message_root.plan.report.events) >= 2
    assert_within_limits(view)


def test_data_exhibit_formats_typed_nodes_rather_than_strings() -> None:
    message_root = MessageRoot(LayoutShowcase(section="data", entries=40, locale="en"), access=Everyone(), timeout=None)
    view = commit_render(message_root)
    content = _texts(view)

    assert "**Sample builds:** 40" in content
    assert "░░░░░░░░░░ 0%" in content, "a proportion, drawn from the value and its maximum"
    assert "<t:" in content, "the instant reaches each reader in their own timezone"
    assert ":R>" in content, "and does so relative to when they read it"
    assert "Fastest" in content, "the table kept its tabular shape"
    assert "Flush piston door" in content, "with every declared row"
    assert_within_limits(view)


async def test_grid_exhibit_keeps_spatial_rows_and_stable_selection_keys() -> None:
    component = LayoutShowcase(section="grid", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    view = commit_render(message_root)
    numbered = [button for button in _buttons(view) if button.label and button.label.isdecimal()]

    assert [button.label for button in numbered] == [str(index) for index in range(1, 13)]
    assert numbered[5].disabled
    assert numbered[10].disabled

    await message_root.dispatch("showcase-grid.cell-0", interaction_harness())

    assert component.grid_pick == "You picked square 1."


async def test_ownership_exhibit_separates_session_owned_and_component_owned_values() -> None:
    component = LayoutShowcase(section="ownership", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("ownership.controlled", interaction_harness())
    await message_root.dispatch("ownership.rating.4", interaction_harness())

    assert component.subscribed is True, "a controlled value only moves through its handler"
    assert component.rating == 4
    assert "\N{BLACK STAR}" * 4 in _texts(commit_render(message_root))

    await message_root.dispatch("ownership.managed", interaction_harness())
    labels = [button.label for button in _buttons(commit_render(message_root))]

    assert "First switch: on" in labels
    assert message_root.presentation.toggles["ownership.managed"].on is True, "the session holds it, not the component"


async def test_forms_exhibit_validates_then_binds_typed_values_and_prefills() -> None:
    component = LayoutShowcase(section="forms", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)
    assert message_root.plan is not None
    binding = message_root.plan.form_bindings["feedback"]

    rejected = await binding.spec.evaluate({"exhibit": "data", "headline": "Readable", "score": "1"})
    caught = [issue.key for issue in rejected.errors if isinstance(issue, sl.forms.FieldError)]
    assert caught == ["detail"], "cross-field validation sees typed values"

    await message_root.dispatch_submit(
        "feedback",
        interaction_harness(),
        binding.spec,
        {"exhibit": "data", "headline": "Typed all the way down", "score": "5"},
        binding.on_submit,
    )

    assert (component.feedback_exhibit, component.feedback_headline, component.feedback_score) == (
        "data",
        "Typed all the way down",
        5,
    )
    view = commit_render(message_root)
    assert "Typed all the way down" in _texts(view)
    assert message_root.plan.form_bindings["feedback"].spec.prefill["headline"] == "Typed all the way down"
    assert_within_limits(view)


async def test_localization_exhibit_escapes_values_and_relocalizes_the_same_root() -> None:
    component = LayoutShowcase(section="localization", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    first = commit_render(message_root)

    assert "\\*shouty title\\*" in _texts(first)
    assert "@\u200beveryone" in _texts(first)

    interaction = interaction_harness()
    await message_root.dispatch("switch-language", interaction)

    assert component.display_locale == "zh-CN"
    assert message_root.localization.locale == "zh-CN"
    assert interaction.response.edit_message.await_count == 1
    edited_view = interaction.response.edit_message.await_args.kwargs["view"]
    assert "但不用重做这条消息" in _texts(edited_view), "the exhibit's own heading, redrawn"


async def test_composed_children_keep_independent_state_and_keys() -> None:
    component = LayoutShowcase(section="composition", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    view = commit_render(message_root)
    ids = {button.custom_id or "" for button in _buttons(view)}

    assert any("left.increment" in custom_id for custom_id in ids)
    assert any("right.increment" in custom_id for custom_id in ids)

    await message_root.dispatch("left.increment", interaction_harness())

    assert component.left.count == 1
    assert component.right.count == 0


async def test_history_exhibit_preserves_a_sibling_write_and_presents_rollback_continuation() -> None:
    component = LayoutShowcase(section="history", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("history.rename", interaction_harness())
    assert component.project_name == "Action Ledger"
    assert component.outcome_result.startswith("Finished cleanly, as change #")

    await message_root.dispatch("history.sibling", interaction_harness())
    await message_root.dispatch("history.undo", interaction_harness())

    assert component.project_name == "Squid, renamed by somebody else"
    assert component.history_result.startswith("Undo refused:")
    assert component.action_history.entries[0].state is sl.runtime.HistoryEntryState.CONFLICTED

    await message_root.dispatch("history.rollback", interaction_harness())

    assert component.project_name == "Squid, renamed by somebody else", "the staged name never published"
    assert component.outcome_result.startswith("Failed and rolled back (handler_exception)")
    assert component.outcome_result.endswith("written afterwards by a fresh action.")

    await message_root.dispatch("history.drop", interaction_harness())
    assert component.action_history.entries == ()
    assert component.project_name == "Squid, renamed by somebody else", "dropping history is not a forced restore"


async def test_replication_exhibit_selectively_undoes_only_the_local_contribution() -> None:
    component = LayoutShowcase(section="replication", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("replication.local", interaction_harness())
    assert component.local_document.counter("votes").value == 2
    assert component.local_document.set("reviewers").value == frozenset({"you"})

    await message_root.dispatch("replication.peer", interaction_harness())
    assert component.local_document.counter("votes").value == 5
    assert component.local_document.set("reviewers").value == frozenset({"you", "them"})
    assert component.peer_document.snapshot() == component.local_document.snapshot()

    await message_root.dispatch("replication.undo", interaction_harness())

    assert component.local_document.counter("votes").value == 3, "their three survive"
    assert component.local_document.set("reviewers").value == frozenset({"them"})
    assert component.replication_result == "Undo worked."


async def test_effects_exhibit_retries_compensation_and_accepts_an_operation_result() -> None:
    component = LayoutShowcase(section="effects", entries=20, locale="en")
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("effects.publish", interaction_harness())
    first_execution = component.publication
    assert first_execution is not None
    assert isinstance(first_execution.status, sl.operations.Succeeded)

    await message_root.dispatch("effects.accept", interaction_harness())
    assert component.published_revision == 41

    await message_root.dispatch("effects.publish", interaction_harness())
    assert component.publication is not None
    assert component.publication.context.execution_id != first_execution.context.execution_id

    await message_root.dispatch("effects.create", interaction_harness())
    await message_root.dispatch("effects.fail", interaction_harness())
    await message_root.dispatch("effects.undo", interaction_harness())

    assert component.channel_service.exists is True
    assert component.channel_present is True
    assert component.compensation_result.startswith("Undo failed")

    await message_root.dispatch("effects.undo", interaction_harness())

    assert component.channel_service.exists is False
    assert component.channel_present is False
    assert component.compensation_result == "Undo worked."


async def test_demo_command_and_controls_are_public() -> None:
    bot = make_layout_bot(
        services=Services(settings=SettingsRecorder()),
        topic_bus=sl.runtime.LocalTopicBus(),
    )
    cog = LayoutShowcaseCog(cast(Any, bot))
    ctx = cast(
        commands.Context[Any],
        ContextHarness(message=MessageHarness(message_id=1), bot=bot, user_id=7).source,
    )

    await LayoutShowcaseCog.demo.callback(cog, ctx, "pagination", 20)  # type: ignore[arg-type]

    sent = cast(Any, ctx).send.await_args.kwargs
    assert sent["ephemeral"] is False
    assert isinstance(sent["view"], discord.ui.LayoutView)
    demo = next(command for command in cog.__cog_commands__ if command.qualified_name == "layout demo")
    assert demo.checks == []


class TestSharedAppearance:
    """The worked example: two live panels agreeing on view state neither of them owns."""

    def panels(
        self,
    ) -> tuple[sl.runtime.LocalTopicBus, MessageRootScheduler, Appearance, Session, MessageRoot, MessageRoot]:
        bus = sl.runtime.LocalTopicBus()
        scheduler = MessageRootScheduler(bus)
        scope = UserScope(7)
        appearance, session = Appearance(bus, scope), Session(bus, scope)
        writer = MessageRoot(AppearancePanel(appearance, session), access=Owner(7), scheduler=scheduler, timeout=None)
        reader = MessageRoot(PreviewPanel(appearance, session), access=Owner(7), scheduler=scheduler, timeout=None)
        return bus, scheduler, appearance, session, writer, reader

    async def test_the_reading_panel_follows_what_it_rendered(self) -> None:
        _, _, appearance, session, writer, reader = self.panels()
        await reader.send(delivered_to(message_harness(message_id=2)))

        assert set(reader.followed) == {
            sl.runtime.CellAddress(appearance, "accent"),
            sl.runtime.CellAddress(appearance, "density"),
            sl.runtime.CellAddress(session, "focus"),
        }
        assert writer.followed == ()

    async def test_a_press_on_one_panel_schedules_the_other(self) -> None:
        bus, scheduler, _, _, writer, reader = self.panels()
        await writer.send(delivered_to(message_harness(message_id=1)))
        await reader.send(delivered_to(message_harness(message_id=2)))

        await writer.dispatch("controls.density", interaction_harness(user_id=7))

        assert reader in scheduler._queued

    async def test_the_injected_namespace_reaches_a_leaf_and_undo_covers_it(self) -> None:
        _, _, appearance, _, writer, _ = self.panels()
        await writer.send(delivered_to(message_harness(message_id=1)))

        await writer.dispatch("controls.density", interaction_harness(user_id=7))
        assert appearance.density == "compact"

        await writer.dispatch("controls.undo", interaction_harness(user_id=7))
        assert appearance.density == "comfortable", "one entry restores state the panel does not own"

    async def test_the_accent_button_declares_its_own_recording(self) -> None:
        """`record=` on the control: the handler only writes, the framework opens the entry."""
        _, _, appearance, _, writer, _ = self.panels()
        await writer.send(delivered_to(message_harness(message_id=1)))
        before = appearance.accent

        await writer.dispatch("controls.accent", interaction_harness(user_id=7))
        assert appearance.accent != before

        await writer.dispatch("controls.undo", interaction_harness(user_id=7))
        assert appearance.accent == before

    async def test_the_session_dies_with_its_panels_and_appearance_does_not(self) -> None:
        import gc
        import weakref

        _, _, appearance, session, writer, reader = self.panels()
        await writer.send(delivered_to(message_harness(message_id=1)))
        await reader.send(delivered_to(message_harness(message_id=2)))
        gone, kept = weakref.ref(session), weakref.ref(appearance)

        await writer.finish(disable=False)
        await reader.finish(disable=False)
        del session, writer, reader
        gc.collect()

        assert gone() is None, "co-existence state: nothing was looking at it"
        assert kept() is appearance, "retention state: the caller still holds it"


def _lobby_root(panel: Lobby) -> MessageRoot:
    return _lobby_session(panel).root


def _lobby_session(panel: Lobby) -> sd.sessions.Session:
    session = panel._session()
    assert session is not None
    return session


class TestLobby:
    """The roster is session membership, so the panel holds none of it."""

    async def opened(
        self,
        *,
        bot: Any | None = None,
        guild_id: int = 5,
        host_id: int = 7,
    ) -> tuple[Any, Lobby]:
        bot = make_layout_bot() if bot is None else bot
        context = ContextHarness(
            message=MessageHarness(message_id=guild_id),
            bot=bot,
            user_id=host_id,
        )
        context.guild = Guild(id=guild_id)
        panel = await Lobby(host_id).show(cast(sd.InvocationSource, context))
        assert panel is not None
        return bot, panel

    async def test_the_host_opens_as_the_only_member(self) -> None:
        bot, panel = await self.opened()

        assert next(iter(bot.sessions.active())).members == frozenset({7})
        assert "### Players — 1/4" in _texts(commit_render(_lobby_root(panel)))

    async def test_a_press_from_anyone_joins_and_the_roster_redraws(self) -> None:
        bot, panel = await self.opened()

        await _lobby_root(panel).dispatch("lobby-roster.players", interaction_harness(user_id=8))

        assert next(iter(bot.sessions.active())).members == frozenset({7, 8})
        assert "### Players — 2/4" in _texts(commit_render(_lobby_root(panel)))

    async def test_the_lobby_fills_and_then_refuses(self) -> None:
        bot, panel = await self.opened()

        for user_id in (8, 9, 10, 11, 12):
            await _lobby_root(panel).dispatch("lobby-roster.players", interaction_harness(user_id=user_id))

        assert next(iter(bot.sessions.active())).members == frozenset({7, 8, 9, 10})

    async def test_a_roster_dependent_rule_closes_the_lobby_when_the_host_leaves(self) -> None:
        bot, panel = await self.opened()
        session = next(iter(bot.sessions.active()))

        await _lobby_root(panel).dispatch("leave", interaction_harness(user_id=7))
        await _lobby_root(panel).dispatch("lobby-roster.players", interaction_harness(user_id=8))

        assert session.members == frozenset()
        assert not session.root.finished

    async def test_only_a_member_may_start(self) -> None:
        """Everyone may press Join, so the control that is not for everyone checks itself."""
        _, panel = await self.opened()

        await _lobby_root(panel).dispatch("start", interaction_harness(user_id=8))
        assert panel.started_with is None

        await _lobby_root(panel).dispatch("lobby-roster.players", interaction_harness(user_id=8))
        await _lobby_root(panel).dispatch("start", interaction_harness(user_id=8))

        assert panel.started_with == 2
        assert "Started with 2 players." in _texts(commit_render(_lobby_root(panel)))

    async def test_a_reader_cannot_hold_a_seat_in_two_servers(self) -> None:
        """Two lobbies, two hosts, one reader: the quota is what stops the second seat."""
        bot, here = await self.opened()
        _, elsewhere = await self.opened(bot=bot, guild_id=6, host_id=9)

        await _lobby_root(elsewhere).dispatch("lobby-roster.players", interaction_harness(user_id=8))
        await _lobby_root(here).dispatch("lobby-roster.players", interaction_harness(user_id=8))

        assert bot.sessions.sessions_for_member(8) == (bot.sessions.sessions_for_member(8)[0],)
        assert 8 in _lobby_session(elsewhere).members
        assert 8 not in _lobby_session(here).members
