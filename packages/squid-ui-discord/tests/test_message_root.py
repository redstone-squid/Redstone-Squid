"""Reactive core tests: state, dispatch funnel, flush, lifecycle."""

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import discord
import pytest
from discord.webhook.async_ import AsyncWebhookAdapter, async_context

import squid_ui as sl
import squid_ui_discord
from squid_reactivity import ActionLedger, add_action_result_sink
from squid_ui import (
    ActionEvent,
    Component,
    Document,
    LayoutNode,
    PressEvent,
    SelectionEvent,
    computed,
    resource,
    state,
)
from squid_ui import form as sl_form
from squid_ui.chrome import LOCALIZATION_CONTEXT, Chrome
from squid_ui.document import Asset, InlineAsset
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormField, FormSpec, TextField
from squid_ui.interactions import ActionMiddleware, ActionMode, ActionProceed, ActionRequest, InteractionKind
from squid_ui.primitives import (
    Button,
    ControlGroup,
    Heading,
    Lines,
    Option,
    Paginate,
    Row,
    SelectMenu,
    Text,
)
from squid_ui.profiling import (
    ActionStatus,
    DispatchDisposition,
    MemoryProfiler,
    OperationKind,
    PresentationStatus,
    RuntimeTrace,
    TraceStatus,
)
from squid_ui.runtime import (
    ComponentRuntime,
    Failed,
    Pending,
    PendingMode,
    ReactiveWriteError,
    Ready,
    batch,
    transaction,
)
from squid_ui.runtime.reactivity import _CURRENT
from squid_ui.semantic import Paragraph
from squid_ui.text import Localization, Message
from squid_ui_discord import (
    Everyone,
    MessageRoot,
    MessageRootScheduler,
    Owner,
    PauseUpdates,
    RenewEphemeral,
    Users,
    delivery,
)
from squid_ui_discord.access import Allowed, Check, Denied
from squid_ui_discord.message_root import MessageRootStatus, _BusyPaint, _custom_id
from squid_ui_discord.testing import (
    assert_within_limits,
    commit_render,
    delivered_to,
    fake_interaction,
    fake_message,
)


class Counter(Component):
    count: int = state(0)

    def render(self):
        return [
            Heading("Counter"),
            Text(f"count: {self.count}"),
            Row((Button(label="+1", on_click=self.increment, key="inc"),)),
        ]

    async def increment(self, event: PressEvent) -> None:
        self.count += 1


@pytest.mark.parametrize("warning", [0, -1, math.inf, -math.inf, math.nan])
def test_expiry_policies_require_a_finite_positive_warning(warning: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        PauseUpdates(warning)
    with pytest.raises(ValueError, match="finite positive"):
        RenewEphemeral(warning)


def test_renewal_policy_requires_an_expiry_supervisor() -> None:
    with pytest.raises(TypeError, match="scheduler"):
        MessageRoot(Counter(), access=Everyone(), expiry=RenewEphemeral())


async def test_message_root_snapshot_reports_lifecycle_and_handle_expiry() -> None:
    now = datetime.now(UTC)
    scheduler = MessageRootScheduler(clock=lambda: now)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=45)
    message_root = MessageRoot(Counter(), access=Everyone(), scheduler=scheduler)
    await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))

    snapshot = message_root.snapshot()

    assert snapshot.lifecycle is MessageRootStatus.ACTIVE
    assert snapshot.handle_expires_in == pytest.approx(45)


class TestCandidateSettlement:
    """A drawn candidate owes the mount exactly one ending: committed, or rolled back."""

    async def test_a_candidate_cannot_be_rolled_back_twice(self) -> None:
        message_root = MessageRoot(Counter(), access=Everyone())
        await message_root.send(delivered_to(fake_message()))
        candidate = message_root._stage()

        message_root._rollback(candidate)

        with pytest.raises(LayoutInvariantError, match="already settled"):
            message_root._rollback(candidate)

    async def test_a_committed_candidate_cannot_be_rolled_back(self) -> None:
        message_root = MessageRoot(Counter(), access=Everyone())
        await message_root.send(delivered_to(fake_message()))
        candidate = message_root._stage()

        message_root._commit(candidate)

        with pytest.raises(LayoutInvariantError, match="already settled"):
            message_root._rollback(candidate)

    async def test_only_one_candidate_may_be_outstanding_at_a_time(self) -> None:
        """The reconciler owns this half: a second draw cannot stage over the first."""
        message_root = MessageRoot(Counter(), access=Everyone())
        await message_root.send(delivered_to(fake_message()))
        message_root._stage()

        with pytest.raises(RuntimeError, match="already staged"):
            message_root._stage()


async def _armed_root(
    component: Component | None = None,
    *,
    access: squid_ui_discord.AccessPolicy | None = None,
    on_error: squid_ui_discord.message_root.ErrorHook | None = None,
) -> tuple[MessageRoot, Any, MessageRootScheduler]:
    now = datetime.now(UTC)
    scheduler = MessageRootScheduler(clock=lambda: now)
    interaction = fake_interaction()
    interaction.expires_at = now + timedelta(seconds=30)
    message_root = MessageRoot(
        Counter() if component is None else component,
        access=Everyone() if access is None else access,
        scheduler=scheduler,
        timeout=None,
        expiry=RenewEphemeral(warning=60),
        on_error=on_error,
    )
    await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(interaction)))
    assert message_root.handle is not None
    message_root._queue_expiry_arm(message_root.handle)
    await message_root.refresh()
    return message_root, interaction, scheduler


class TestEphemeralRenewal:
    async def test_arming_replaces_a_full_layout_without_committing_application_state(self) -> None:
        component = RootToolbar()
        subject, interaction, _ = await _armed_root(component)
        runtime_revision = subject.runtime.revision

        written = interaction.response.edit_message.await_args.kwargs
        payload = str(written["view"].to_components())

        assert "This session is about to expire" in payload
        assert "Continue Session" in payload
        assert "attachments" not in written
        assert subject.snapshot().lifecycle is MessageRootStatus.RENEWAL_ARMED
        assert subject.runtime.revision == runtime_revision
        assert subject.pending is False
        assert subject.generation == 2

    async def test_refreshes_freeze_behind_the_renewal_screen_without_rendering(self) -> None:
        class Counting(Component):
            def __init__(self) -> None:
                self.renders = 0

            def render(self):
                self.renders += 1
                return Text(f"render {self.renders}")

        component = Counting()
        message_root, interaction, _ = await _armed_root(component)
        rendered = component.renders
        interaction.response.edit_message.reset_mock()

        message_root.invalidate()
        await message_root.refresh()

        assert component.renders == rendered
        assert message_root.pending
        interaction.response.edit_message.assert_not_awaited()
        assert message_root.snapshot().lifecycle is MessageRootStatus.RENEWAL_ARMED

    async def test_renewal_restores_latest_state_on_the_same_message(self) -> None:
        component = Counter()
        message_root, _, _ = await _armed_root(component)
        component.count = 7
        generation = message_root.generation
        interaction = fake_interaction(message_id=99)

        await message_root.dispatch("__squid_continue_session", interaction, generation=generation)

        payload = str(interaction.response.edit_message.await_args.kwargs["view"].to_components())
        assert "count: 7" in payload
        assert message_root.snapshot().lifecycle is MessageRootStatus.ACTIVE
        assert message_root.handle is not None and message_root.handle.expires_at == interaction.expires_at
        assert not message_root.pending

    async def test_denied_renewal_keeps_the_screen_and_old_authority(self) -> None:
        message_root, _, _ = await _armed_root(access=Owner(1))
        original = message_root.handle
        interaction = fake_interaction(user_id=2)

        await message_root.dispatch("__squid_continue_session", interaction, generation=message_root.generation)

        interaction.response.send_message.assert_awaited_once()
        assert message_root.handle is original
        assert message_root.snapshot().lifecycle is MessageRootStatus.RENEWAL_ARMED

    async def test_failed_renewal_keeps_fresh_authority_and_is_retryable(self) -> None:
        errors = AsyncMock()
        message_root, _, _ = await _armed_root(on_error=errors)
        generation = message_root.generation
        failed = fake_interaction()
        failed.response.edit_message.side_effect = RuntimeError("Discord refused the restore")

        await message_root.dispatch("__squid_continue_session", failed, generation=generation)

        errors.assert_awaited_once()
        assert errors.await_args is not None
        assert errors.await_args.args[2] == "renewal"
        assert message_root.handle is not None and message_root.handle.expires_at == failed.expires_at
        assert message_root.snapshot().lifecycle is MessageRootStatus.RENEWAL_ARMED
        retry = fake_interaction()
        await message_root.dispatch("__squid_continue_session", retry, generation=generation)
        retry.response.edit_message.assert_awaited_once()
        assert message_root.snapshot().lifecycle is MessageRootStatus.ACTIVE

    async def test_repeated_renewal_click_is_acknowledged_without_a_second_commit(self) -> None:
        message_root, _, _ = await _armed_root()
        generation = message_root.generation
        first = fake_interaction()
        await message_root.dispatch("__squid_continue_session", first, generation=generation)
        active_generation = message_root.generation
        repeated = fake_interaction()

        await message_root.dispatch("__squid_continue_session", repeated, generation=generation)

        repeated.response.defer.assert_awaited_once()
        repeated.response.edit_message.assert_not_awaited()
        assert message_root.generation == active_generation

    async def test_permanent_authority_disarms_and_restores_the_application(self) -> None:
        component = Counter()
        message_root, _, _ = await _armed_root(component)
        component.count = 4
        message = fake_message(ephemeral=False)

        await message_root.adopt_handle(delivery.handle_for(message))

        payload = str(message.edit.await_args.kwargs["view"].to_components())
        assert "count: 4" in payload
        assert message_root.handle is not None and message_root.handle.permanent
        assert message_root.snapshot().lifecycle is MessageRootStatus.ACTIVE

    async def test_finishing_while_armed_does_not_reconstruct_the_hidden_tree(self) -> None:
        class Counting(Component):
            def __init__(self) -> None:
                self.renders = 0

            def render(self):
                self.renders += 1
                return Text(f"render {self.renders}")

        component = Counting()
        message_root, _, _ = await _armed_root(component)
        rendered = component.renders

        await message_root.finish()

        assert component.renders == rendered
        assert message_root.finished

    async def test_stale_arming_leaves_the_application_generation_pending(self) -> None:
        message_root, interaction, _ = await _armed_root()
        # Restore active state so this test can exercise the stale arm branch independently.
        await message_root.dispatch("__squid_continue_session", fake_interaction(), generation=message_root.generation)
        active_generation = message_root.generation

        class StaleHandle:
            permanent = False
            expires_at: datetime | None = datetime.now(UTC) + timedelta(seconds=30)
            mode = squid_ui_discord.DiscordMode.COMPONENTS_V2

            def expired(self) -> bool:
                return False

            async def write(self, *args: Any, **kwargs: Any) -> None:
                raise delivery.StaleHandleError("expired")

        stale = StaleHandle()
        message_root._handle = stale
        message_root._queue_expiry_arm(stale)
        interaction.response.edit_message.reset_mock()

        await message_root.refresh()

        assert message_root.snapshot().lifecycle is MessageRootStatus.ACTIVE
        assert message_root.generation == active_generation
        assert message_root.pending
        assert message_root.handle is None


class RootToolbar(Component):
    def render(self):
        return Document(
            (ControlGroup(tuple(Button(str(index), self.click, f"b{index}") for index in range(41))),),
            key="toolbar",
        )

    async def click(self, event: PressEvent) -> None: ...


class Child(Component):
    def __init__(self, mounted: list[str]) -> None:
        self.mounted = mounted

    def render(self):
        return Text("child")

    def on_mount(self) -> None:
        self.mounted.append("child")


class Panel(Component):
    """A pager, a button and an optional child — one of each thing a commit publishes."""

    entries: tuple[str, ...] = state(factory=lambda: tuple(f"entry {index}" for index in range(6)))
    show_child: bool = state(default=False)

    def __init__(self, mounted: list[str]) -> None:
        self.child = Child(mounted)

    def render(self):
        nodes: list[LayoutNode] = [
            Lines(self.entries, overflow=Paginate(key="entries", per=2)),
            Row((Button("add", self.add, "add"),)),
        ]
        if self.show_child:
            nodes.append(self.boundary(self.child, key="child"))
        return nodes

    async def add(self, event: PressEvent) -> None:
        self.entries = (*self.entries, "added")
        self.show_child = True


def _http_error() -> discord.HTTPException:
    response = cast(Any, SimpleNamespace(status=500, reason="Internal Server Error"))
    return discord.HTTPException(response, "edit refused")


def _stale_http_error() -> discord.HTTPException:
    response = cast(Any, SimpleNamespace(status=404, reason="Not Found"))
    return discord.HTTPException(response, {"code": 10015, "message": "Unknown Webhook"})


async def _refuse_edit(*args: Any, **kwargs: Any) -> None:
    raise _http_error()


class _RefusingHandle:
    """An edit handle Discord rejects for a reason that is not staleness."""

    permanent = False
    expires_at = None
    mode = squid_ui_discord.DiscordMode.COMPONENTS_V2

    def expired(self) -> bool:
        return False

    async def write(self, *args: Any, **kwargs: Any) -> None:
        raise _http_error()


def _refuse_handle(*args: Any, **kwargs: Any) -> _RefusingHandle:
    return _RefusingHandle()


def _button(view: discord.ui.LayoutView) -> discord.ui.Button:
    return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button))


def _profile_trace(profiler: MemoryProfiler) -> RuntimeTrace:
    snapshot = profiler.snapshot()
    traces = (*snapshot.recent, *snapshot.slow, *snapshot.failed, *snapshot.deadline_misses)
    unique = {trace.trace_id: trace for trace in traces}
    assert len(unique) == 1
    return next(iter(unique.values()))


def _operation_trace(profiler: MemoryProfiler, operation: OperationKind) -> RuntimeTrace:
    snapshot = profiler.snapshot()
    traces = (*snapshot.recent, *snapshot.slow, *snapshot.failed, *snapshot.deadline_misses)
    matches = {trace.trace_id: trace for trace in traces if trace.operation is operation}
    assert len(matches) == 1
    return next(iter(matches.values()))


class TestDispatchProfiling:
    async def test_dispatch_names_the_actor_on_the_root_span(self) -> None:
        profiler = MemoryProfiler()
        message_root = MessageRoot(Counter(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(user_id=77))

        root = next(span for span in _profile_trace(profiler).spans if span.parent_span_id is None)
        assert ("actor", 77) in {(attribute.key, attribute.value) for attribute in root.attributes}

    async def test_handler_span_links_to_the_semantic_action_identity(self) -> None:
        profiler = MemoryProfiler()
        ledger = ActionLedger()
        add_action_result_sink(ledger)
        message_root = MessageRoot(Counter(), access=Everyone(), profiler=profiler, timeout=None)
        commit_render(message_root)

        try:
            await message_root.dispatch("inc", fake_interaction())
        finally:
            ledger.close()

        handler = next(span for span in _profile_trace(profiler).spans if span.name == "handler")
        action_id = dict((attribute.key, attribute.value) for attribute in handler.attributes)["action_id"]
        assert any(result.action_id == action_id for result in ledger.results)

    async def test_success_records_action_presentation_generation_and_stages(self) -> None:
        profiler = MemoryProfiler()
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), profiler=profiler, timeout=None)
        commit_render(message_root)
        submitted = message_root.generation

        await message_root.dispatch("inc", fake_interaction(), generation=submitted)

        trace = _profile_trace(profiler)
        result = trace.result.dispatch
        assert result is not None
        assert result.disposition is DispatchDisposition.COMPLETED
        assert result.action is ActionStatus.HANDLED
        assert result.presentation is PresentationStatus.WRITTEN
        assert result.generation.submitted == submitted
        assert result.generation.active == submitted
        assert not result.generation.rebased
        aggregate = profiler.snapshot().aggregates[0]
        assert aggregate.key.disposition is DispatchDisposition.COMPLETED
        assert aggregate.key.action is ActionStatus.HANDLED
        assert aggregate.key.presentation is PresentationStatus.WRITTEN
        assert {span.name for span in trace.spans} >= {
            "acknowledgement",
            "access",
            "binding",
            "action_lock",
            "handler",
            "flush",
            "runtime_render",
            "planner",
            "renderer",
            "discord_write",
            "commit",
        }
        planner = next(span for span in trace.spans if span.name == "planner")
        assert {attribute.key for attribute in planner.attributes} == {
            "cache_hit",
            "reuse",
            "states_explored",
            "search_fallback",
        }
        acknowledgement = next(span for span in trace.spans if span.name == "acknowledgement")
        assert dict((attribute.key, attribute.value) for attribute in acknowledgement.attributes) == {
            "source": "interaction_write"
        }

    async def test_stale_and_rebased_are_distinct_generation_metadata(self) -> None:
        stale_profiler = MemoryProfiler()
        stale_root = MessageRoot(Counter(), access=Everyone(), profiler=stale_profiler, timeout=None)
        commit_render(stale_root)
        submitted = stale_root.generation
        commit_render(stale_root)

        await stale_root.dispatch("inc", fake_interaction(), generation=submitted)

        stale = _profile_trace(stale_profiler).result.dispatch
        assert stale is not None
        assert stale.disposition is DispatchDisposition.STALE
        assert not stale.generation.rebased

        class Rebased(Component):
            def render(self):
                return Row((Button("run", self.run, "run", mode=ActionMode.REBASE),))

            async def run(self, event: PressEvent) -> None: ...

        rebase_profiler = MemoryProfiler()
        rebase_root = MessageRoot(Rebased(), access=Everyone(), profiler=rebase_profiler, timeout=None)
        commit_render(rebase_root)
        submitted = rebase_root.generation
        commit_render(rebase_root)

        await rebase_root.dispatch("run", fake_interaction(), generation=submitted)

        rebased = _profile_trace(rebase_profiler).result.dispatch
        assert rebased is not None
        assert rebased.disposition is DispatchDisposition.COMPLETED
        assert rebased.generation.rebased

    async def test_short_circuit_and_recovered_handler_failure_remain_visible(self) -> None:
        class Stop(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None: ...

        stopped_profiler = MemoryProfiler()
        stopped = MessageRoot(
            Counter(),
            access=Everyone(),
            middleware=(Stop(),),
            profiler=stopped_profiler,
            timeout=None,
        )
        commit_render(stopped)
        await stopped.dispatch("inc", fake_interaction())

        stopped_result = _profile_trace(stopped_profiler).result.dispatch
        assert stopped_result is not None
        assert stopped_result.action is ActionStatus.SHORT_CIRCUITED

        class Broken(Counter):
            async def increment(self, event: PressEvent) -> None:
                self.count = 1
                raise RuntimeError("caught")

        class Catch(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                with pytest.raises(RuntimeError):
                    await proceed()

        recovered_profiler = MemoryProfiler()
        recovered = MessageRoot(
            Broken(),
            access=Everyone(),
            middleware=(Catch(),),
            profiler=recovered_profiler,
            timeout=None,
        )
        commit_render(recovered)
        await recovered.dispatch("inc", fake_interaction())

        recovered_trace = _profile_trace(recovered_profiler)
        recovered_result = recovered_trace.result.dispatch
        assert recovered_result is not None
        assert recovered_result.disposition is DispatchDisposition.COMPLETED
        assert recovered_result.action is ActionStatus.HANDLED
        handler = next(span for span in recovered_trace.spans if span.name == "handler")
        assert handler.status is TraceStatus.FAILED

    async def test_action_and_delivery_failures_have_different_dispositions(self, monkeypatch) -> None:
        class Broken(Counter):
            async def increment(self, event: PressEvent) -> None:
                raise RuntimeError("action failed")

        action_profiler = MemoryProfiler()
        failed_action = MessageRoot(
            Broken(),
            access=Everyone(),
            profiler=action_profiler,
            on_error=AsyncMock(),
            timeout=None,
        )
        commit_render(failed_action)
        await failed_action.dispatch("inc", fake_interaction())

        action = _profile_trace(action_profiler).result.dispatch
        assert action is not None
        assert action.disposition is DispatchDisposition.ACTION_FAILED
        assert action.action is ActionStatus.FAILED

        delivery_profiler = MemoryProfiler()
        failed_delivery = MessageRoot(Counter(), access=Everyone(), profiler=delivery_profiler, timeout=None)
        commit_render(failed_delivery)
        monkeypatch.setattr(delivery, "handle_from", _refuse_handle)
        with pytest.raises(discord.HTTPException):
            await failed_delivery.dispatch("inc", fake_interaction())

        delivered = _profile_trace(delivery_profiler).result.dispatch
        assert delivered is not None
        assert delivered.disposition is DispatchDisposition.DELIVERY_FAILED
        assert delivered.action is ActionStatus.HANDLED
        assert delivered.presentation is PresentationStatus.FAILED

    async def test_watchdog_records_deadline_miss_and_acknowledgement_source(self) -> None:
        started = anyio.Event()
        release = anyio.Event()

        class Slow(Component):
            def render(self):
                return Row((Button("slow", self.slow, "slow"),))

            async def slow(self, event: PressEvent) -> None:
                started.set()
                await release.wait()

        profiler = MemoryProfiler()
        message_root = MessageRoot(
            Slow(),
            access=Everyone(),
            profiler=profiler,
            timeout=None,
            acknowledgement_timeout=0.01,
        )
        commit_render(message_root)
        interaction = fake_interaction()

        async def dispatch() -> None:
            await message_root.dispatch("slow", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch)
            await started.wait()
            await anyio.sleep(0.02)
            release.set()

        trace = _profile_trace(profiler)
        acknowledgement = next(span for span in trace.spans if span.name == "acknowledgement")
        assert dict((attribute.key, attribute.value) for attribute in acknowledgement.attributes) == {
            "source": "watchdog"
        }
        assert trace.deadline_missed


class TestRenderAndWire:
    def test_stage_view_wires_handlers(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        view = commit_render(message_root)
        button = _button(view)
        assert button.custom_id is not None and button.custom_id.startswith(f"ctl:{message_root.id}:1:inc")
        assert "inc" in message_root._handlers
        assert_within_limits(view)

    def test_render_generations_have_distinct_control_ids(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)

        first = _button(commit_render(message_root))
        second = _button(commit_render(message_root))

        assert first.custom_id != second.custom_id

    def test_custom_id_digests_do_not_collide_across_a_shared_prefix(self):
        shared_prefix = "section." * 20
        first = _custom_id("mount", 1, shared_prefix + "one")
        second = _custom_id("mount", 1, shared_prefix + "two")

        assert len(first) <= 100
        assert len(second) <= 100
        assert first != second

    async def test_keyed_document_root_pages_are_live_message_root_navigation(self):
        message_root = MessageRoot(RootToolbar(), access=Everyone(), timeout=None)
        commit_render(message_root)

        assert message_root.presentation.cursor("toolbar").extent > 1
        await message_root.dispatch("__cursor_next.toolbar", fake_interaction())
        assert message_root.presentation.cursor("toolbar").position.offset == 1

    async def test_click_mutates_state_and_edits(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        assert component.count == 1
        edited_view = interaction.response.edit_message.await_args.kwargs["view"]
        texts = [i.content for i in edited_view.walk_children() if isinstance(i, discord.ui.TextDisplay)]
        assert "count: 1" in texts

    async def test_press_event_carries_portable_actor_and_frontend_context(self):
        seen: list[PressEvent] = []

        class Inspect(Component):
            def render(self):
                return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

            async def inspect(self, event: PressEvent) -> None:
                seen.append(event)

        message_root = MessageRoot(Inspect(), access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inspect", fake_interaction(user_id=42))

        assert seen[0].actor.id == "42"
        assert seen[0].context == {"frontend": "discord"}

    async def test_press_event_carries_the_mounts_negotiated_locale(self):
        seen: list[PressEvent] = []

        class Inspect(Component):
            def render(self):
                return Row((Button(label="inspect", on_click=self.inspect, key="inspect"),))

            async def inspect(self, event: PressEvent) -> None:
                seen.append(event)

        message_root = MessageRoot(Inspect(), access=Everyone(), localization=Localization("zh-CN"), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inspect", fake_interaction())

        assert seen[0].locale == "zh-CN"

    def test_localize_retranslates_content_chrome_and_runtime_context(self):
        class Localized(Component):
            def render(self):
                return [
                    Paragraph(Message("Hello")),
                    Lines(("a", "b"), overflow=Paginate(key="lines", per=1)),
                ]

        translated = {"Hello": "Bonjour", "Previous": "Précédent", "Next": "Suivant"}
        chrome = Chrome(previous=Message("Previous"), next=Message("Next"))
        message_root = MessageRoot(
            Localized(), access=Everyone(), chrome=chrome, localization=Localization("en"), timeout=None
        )
        commit_render(message_root)

        localization = Localization("fr", gettext=lambda message: translated.get(message, message))
        message_root.localize(localization)
        view = commit_render(message_root)

        texts = [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
        labels = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        assert "Bonjour" in texts
        assert labels == ["Précédent", "Suivant"]
        assert message_root.runtime.context[LOCALIZATION_CONTEXT] is localization

    async def test_notice_resolves_deferred_text_with_message_root_localization(self):
        class Notify(Component):
            def render(self):
                return Row((Button(label="notify", on_click=self.notify, key="notify"),))

            async def notify(self, event: PressEvent) -> None:
                await event.notice(Message("Notice"))

        localization = Localization("fr", gettext=lambda message: "Avis" if message == "Notice" else message)
        message_root = MessageRoot(Notify(), access=Everyone(), localization=localization, timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("notify", interaction)

        interaction.response.send_message.assert_awaited_once()
        notice = interaction.response.send_message.await_args.kwargs["view"]
        assert [item.content for item in notice.walk_children() if isinstance(item, discord.ui.TextDisplay)] == ["Avis"]

    async def test_clean_dispatch_defers_instead_of_editing(self):
        class Static(Counter):
            async def increment(self, event: PressEvent) -> None:
                pass  # no state change

        message_root = MessageRoot(Static(), access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()

    async def test_stale_key_is_acknowledged_not_crashed(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("gone", interaction)

        interaction.response.defer.assert_awaited_once()

    async def test_slow_handler_is_acknowledged_by_the_runtime_watchdog(self):
        started = anyio.Event()
        release = anyio.Event()

        class Slow(Component):
            def render(self):
                return Row((Button("slow", self.slow, "slow"),))

            async def slow(self, event: PressEvent) -> None:
                started.set()
                await release.wait()

        message_root = MessageRoot(Slow(), access=Everyone(), timeout=None, acknowledgement_timeout=0.01)
        commit_render(message_root)
        interaction = fake_interaction()

        async def dispatch() -> None:
            await message_root.dispatch("slow", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch)
            await started.wait()
            await anyio.sleep(0.02)
            interaction.response.defer.assert_awaited_once()
            interaction.response._done = True
            release.set()


class TestAccessPolicy:
    def test_access_is_a_required_keyword(self) -> None:
        assert inspect.signature(MessageRoot).parameters["access"].default is inspect.Parameter.empty

    async def test_everyone_admits_any_user(self) -> None:
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(user_id=99))

        assert component.count == 1

    async def test_wrong_user_is_rejected_ephemerally(self):
        now = 0.0
        component = Counter()
        message_root = MessageRoot(component, access=Owner(42), timeout=30, clock=lambda: now)
        commit_render(message_root)
        now = 10.0
        interaction = fake_interaction(user_id=99)

        await message_root.dispatch("inc", interaction)

        assert component.count == 0
        assert message_root.snapshot().idle == 10
        send = interaction.response.send_message
        assert send.await_args.kwargs["ephemeral"] is True

    async def test_owner_passes(self):
        component = Counter()
        message_root = MessageRoot(component, access=Owner(42), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(user_id=42))

        assert component.count == 1

    async def test_a_set_admits_every_member(self):
        component = Counter()
        message_root = MessageRoot(component, access=Users({42, 43}), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(user_id=42))
        await message_root.dispatch("inc", fake_interaction(user_id=43))

        assert component.count == 2

    async def test_a_set_still_rejects_a_stranger(self):
        component = Counter()
        message_root = MessageRoot(component, access=Users({42, 43}), timeout=None)
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(user_id=99))

        assert component.count == 0

    async def test_access_policies_are_visible_in_snapshots(self):
        assert isinstance(MessageRoot(Counter(), access=Owner(42), timeout=None).snapshot().access, Owner)
        assert MessageRoot(Counter(), access=Users({42, 43}), timeout=None).snapshot().access == Users({42, 43})

    async def test_async_check_can_admit_an_interaction(self) -> None:
        check = AsyncMock(return_value=Allowed())
        component = Counter()
        message_root = MessageRoot(component, access=Check(check), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction(user_id=42)

        await message_root.dispatch("inc", interaction)

        assert component.count == 1
        check.assert_awaited_once_with(interaction)

    async def test_explicit_denial_reason_is_localized(self) -> None:
        async def deny(interaction: discord.Interaction):
            return Denied(Message("Policy denied"))

        localization = Localization("fr", gettext=lambda text: "Refusé" if text == "Policy denied" else text)
        message_root = MessageRoot(Counter(), access=Check(deny), localization=localization, timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        view = interaction.response.send_message.await_args.kwargs["view"]
        assert [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)] == ["Refusé"]

    async def test_policy_errors_use_the_message_root_error_funnel_without_admitting(self) -> None:
        error = RuntimeError("authorization service unavailable")
        check = AsyncMock(side_effect=error)
        hook = AsyncMock()
        component = Counter()
        message_root = MessageRoot(component, access=Check(check), timeout=None, on_error=hook)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        assert component.count == 0
        hook.assert_awaited_once_with(interaction, error, "access")

    async def test_modal_submissions_pass_through_the_same_policy(self) -> None:
        submitted = AsyncMock()
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))
        message_root = MessageRoot(Counter(), access=Owner(42), timeout=None)
        interaction = fake_interaction(user_id=99)

        await message_root.dispatch_submit("rename", interaction, spec, {"name": "Ada"}, submitted)

        submitted.assert_not_awaited()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


class TestFinishHooks:
    async def test_a_hook_fires_on_finish(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))

        await message_root.finish(disable=False)

        assert seen == [message_root]

    async def test_a_hook_fires_on_finish_via(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))
        commit_render(message_root)

        await message_root.finish_via(fake_interaction())

        assert seen == [message_root]

    async def test_a_hook_fires_on_timeout(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))

        await message_root.handle_timeout()

        assert seen == [message_root]

    async def test_a_hook_fires_once_across_repeated_finishes(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))

        await message_root.finish(disable=False)
        await message_root.finish(disable=False)
        await message_root.handle_timeout()

        assert seen == [message_root]

    async def test_hooks_run_in_registration_order(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        order: list[str] = []
        message_root.on_finish(lambda _: _note(order, "first"))
        message_root.on_finish(lambda _: _note(order, "second"))

        await message_root.finish(disable=False)

        assert order == ["first", "second"]

    async def test_a_raising_hook_does_not_stop_the_others_or_teardown(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []

        async def explode(_: MessageRoot) -> None:
            raise RuntimeError("observer is broken")

        message_root.on_finish(explode)
        message_root.on_finish(lambda finished: _record(seen, finished))
        commit_render(message_root)

        await message_root.finish(disable=False)

        assert seen == [message_root]
        assert message_root.finished
        assert message_root._view is None

    async def test_a_hook_fires_even_when_the_disable_edit_raises(self):
        """The mount is finished and torn down either way, so an observer must hear about it.

        `finish_via` re-raises past its own `finally`, which is where the hooks have to run.
        """
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))
        commit_render(message_root)
        interaction = fake_interaction()
        interaction.response.edit_message = AsyncMock(side_effect=RuntimeError("gateway is down"))

        with pytest.raises(RuntimeError):
            await message_root.finish_via(interaction)

        assert seen == [message_root]
        assert message_root.finished

    async def test_a_hook_fires_even_when_finish_hits_an_unanticipated_error(self):
        """`finish` anticipates `HTTPException` from its disable-edit and nothing else.

        Anything it did not anticipate used to propagate past the teardown as well as the
        hooks, leaving the mount half-finished and every observer holding it.
        """
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []
        message_root.on_finish(lambda finished: _record(seen, finished))
        message: Any = fake_message()
        message.edit = AsyncMock(side_effect=RuntimeError("message is gone"))
        await message_root.send(delivered_to(message))

        with pytest.raises(RuntimeError):
            await message_root.finish()

        assert seen == [message_root]
        assert message_root.finished
        assert message_root._view is None

    async def test_finishing_from_inside_a_hook_does_not_recurse(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        calls: list[int] = []

        async def finish_again(finished: MessageRoot) -> None:
            calls.append(1)
            await finished.finish(disable=False)

        message_root.on_finish(finish_again)

        await message_root.finish(disable=False)

        assert calls == [1]

    async def test_finished_flips_only_once_the_message_root_is_done(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)

        assert not message_root.finished

        await message_root.finish(disable=False)

        assert message_root.finished

    async def test_a_late_click_never_reaches_the_handler(self):
        """`view.stop()` hides this in production; a superseded-but-visible message does not."""
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.finish(disable=False)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        assert component.count == 0
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


class TestPresentedHooks:
    async def test_written_and_suppressed_renders_have_distinct_observer_boundaries(self) -> None:
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        committed: list[MessageRoot] = []
        presented: list[MessageRoot] = []
        message_root.on_committed(committed.append)
        message_root.on_presented(presented.append)

        await message_root.send(delivered_to(fake_message()))

        assert committed == [message_root]
        assert presented == [message_root]
        committed.clear()
        presented.clear()

        assert await message_root.refresh() is PresentationStatus.UNCHANGED
        assert committed == [message_root]
        assert presented == []

    async def test_a_hook_can_invalidate_and_the_message_root_remains_usable(self) -> None:
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        message = fake_message()
        calls = 0

        def invalidate_once(presented: MessageRoot) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                presented.invalidate()

        message_root.on_presented(invalidate_once)

        await message_root.send(delivered_to(message))
        assert calls == 1
        assert message_root.pending

        await message_root.refresh()
        assert calls == 1
        assert not message_root.pending
        assert message_root.snapshot().suppressed == 1

    async def test_a_suppressed_refresh_does_not_fire_presented_hooks(self) -> None:
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        presented: list[MessageRoot] = []
        message_root.on_presented(presented.append)
        await message_root.send(delivered_to(fake_message()))
        presented.clear()

        assert await message_root.refresh() is PresentationStatus.UNCHANGED

        assert presented == []

    async def test_a_raising_hook_is_logged_and_does_not_stop_later_hooks(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        seen: list[MessageRoot] = []

        def explode(_: MessageRoot) -> None:
            raise RuntimeError("observer is broken")

        message_root.on_presented(explode)
        message_root.on_presented(seen.append)

        with caplog.at_level("ERROR"):
            await message_root.send(delivered_to(fake_message()))

        assert seen == [message_root]
        assert "presented hook failed" in caplog.text
        assert not message_root.pending


async def _record(seen: list[MessageRoot], message_root: MessageRoot) -> None:
    seen.append(message_root)


async def _note(order: list[str], label: str) -> None:
    order.append(label)


class TestActionPolicy:
    async def test_exclusive_action_from_a_stale_view_is_acknowledged_without_running(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        stale_generation = message_root._generation
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction, generation=stale_generation)

        assert component.count == 0
        interaction.response.defer.assert_awaited_once()

    async def test_rebase_action_uses_the_handler_from_the_current_generation(self):
        calls: list[str] = []

        class Rebased(Component):
            current = False

            def render(self):
                handler = self.new if self.current else self.old
                return Row((Button("run", handler, "run", mode=ActionMode.REBASE),))

            async def old(self, event: PressEvent) -> None:
                calls.append("old")

            async def new(self, event: PressEvent) -> None:
                calls.append("new")

        component = Rebased()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        stale_generation = message_root._generation
        component.current = True
        commit_render(message_root)

        await message_root.dispatch("run", fake_interaction(), generation=stale_generation)

        assert calls == ["new"]

    async def test_rebase_submit_uses_the_form_from_the_current_generation(self):
        calls: list[str] = []
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))

        class Rebased(Component):
            current = False

            def render(self):
                handler = self.new if self.current else self.old
                return sl_form("Rename", spec, key="rename", on_submit=handler, mode=ActionMode.REBASE)

            async def old(self, event) -> None:
                calls.append("old")

            async def new(self, event) -> None:
                calls.append("new")

        component = Rebased()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        stale = message_root.generation
        component.current = True
        commit_render(message_root)

        await message_root.dispatch_submit(
            "rename",
            fake_interaction(),
            spec,
            {"name": "Ada"},
            component.old,
            mode=ActionMode.REBASE,
            generation=stale,
        )

        assert calls == ["new"]

    async def test_submit_declaratively_records_the_whole_action(self):
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))

        class Editor(Component):
            history: sl.runtime.History = sl.runtime.history()
            name: str = state("old")

            def render(self):
                return sl_form(
                    "Rename",
                    spec,
                    key="rename",
                    on_submit=self.rename,
                    record=self.history,
                )

            async def rename(self, event) -> None:
                self.name = event.values["name"]

        editor = Editor()
        message_root = MessageRoot(editor, access=Everyone(), timeout=None)
        commit_render(message_root)
        binding = message_root._form_bindings["rename"]

        await message_root.dispatch_submit(
            "rename",
            fake_interaction(),
            spec,
            {"name": "new"},
            binding.on_submit,
            mode=binding.mode,
            label=binding.label,
            record=binding.record,
        )
        result = await editor.history.undo()

        assert result.applied
        assert editor.name == "old"

    async def test_rebase_submit_never_resolves_the_button_that_opens_the_form(self):
        """`_handlers` holds the presenting button under the very same key."""
        submitted: list[str] = []
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))

        class Trigger(Component):
            def render(self):
                return sl_form("Rename", spec, key="rename", on_submit=self.submit, mode=ActionMode.REBASE)

            async def submit(self, event) -> None:
                submitted.append("submit")

        component = Trigger()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch_submit(
            "rename",
            interaction,
            spec,
            {"name": "Ada"},
            component.submit,
            mode=ActionMode.REBASE,
            generation=message_root.generation,
        )

        # The presenting button would have reopened the modal instead of submitting it.
        assert submitted == ["submit"]
        interaction.response.send_modal.assert_not_awaited()

    async def test_rebase_submit_keeps_the_filled_in_form_when_the_schema_changed_shape(self):
        calls: list[object] = []
        filled = FormSpec("Rename", (TextField(key="name", label="Name"),))
        reshaped = FormSpec("Rename", (TextField(key="title", label="Title"),))

        class Reshaped(Component):
            def render(self):
                return sl_form("Rename", reshaped, key="rename", on_submit=self.new, mode=ActionMode.REBASE)

            async def old(self, event) -> None:
                calls.append(dict(event.values))

            async def new(self, event) -> None:
                calls.append("new")

        component = Reshaped()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)

        await message_root.dispatch_submit(
            "rename",
            fake_interaction(),
            filled,
            {"name": "Ada"},
            component.old,
            mode=ActionMode.REBASE,
            generation=message_root.generation,
        )

        # Parsed against the schema the reader actually saw, not the one that replaced it.
        assert calls == [{"name": "Ada"}]

    async def test_exclusive_submit_still_rejects_a_stale_generation(self):
        calls: list[str] = []
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))

        async def submit(event) -> None:
            calls.append("submit")

        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        commit_render(message_root)
        stale = message_root.generation
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch_submit("rename", interaction, spec, {"name": "Ada"}, submit, generation=stale)

        assert calls == []
        interaction.response.defer.assert_awaited_once()

    async def test_exclusive_actions_do_not_overlap(self):
        active = 0
        maximum = 0

        class Serialized(Component):
            def render(self):
                return Row((Button("run", self.run, "run"),))

            async def run(self, event: PressEvent) -> None:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                await anyio.sleep(0)
                active -= 1

        message_root = MessageRoot(Serialized(), access=Everyone(), timeout=None)
        commit_render(message_root)

        async def dispatch(interaction) -> None:
            await message_root.dispatch("run", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch, fake_interaction())
            tasks.start_soon(dispatch, fake_interaction())

        assert maximum == 1

    async def test_parallel_read_rolls_back_and_reports_state_writes(self):
        class Reader(Component):
            count: int = state(0)

            def render(self):
                return Row((Button("read", self.read, "read", mode=ActionMode.PARALLEL_READ),))

            async def read(self, event: PressEvent) -> None:
                self.count += 1

        component = Reader()
        hook = AsyncMock()
        message_root = MessageRoot(component, access=Everyone(), timeout=None, on_error=hook)
        commit_render(message_root)

        await message_root.dispatch("read", fake_interaction())

        assert component.count == 0
        assert hook.await_args is not None
        assert isinstance(hook.await_args.args[1], ReactiveWriteError)


class TestActionMiddleware:
    def test_the_base_class_requires_dispatch(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            cast(Any, ActionMiddleware)()

    async def test_middleware_is_outermost_first_and_repeated_instances_are_idempotent(self) -> None:
        seen: list[str] = []

        class Record(ActionMiddleware):
            def __init__(self, name: str) -> None:
                self.name = name

            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                seen.append(f"{self.name}:before")
                await proceed()
                seen.append(f"{self.name}:after")

        class Subject(Counter):
            async def increment(self, event: PressEvent) -> None:
                seen.append("handler")
                await super().increment(event)

        first = Record("first")
        message_root = MessageRoot(
            Subject(),
            access=Everyone(),
            middleware=(first, first, Record("second")),
            timeout=None,
        )
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction())

        assert seen == ["first:before", "second:before", "handler", "second:after", "first:after"]

    async def test_short_circuit_skips_the_handler_and_still_acknowledges(self) -> None:
        class Stop(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None: ...

        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), middleware=(Stop(),), timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        assert component.count == 0
        interaction.response.defer.assert_awaited_once_with()

    async def test_middleware_may_handle_a_rolled_back_handler_error(self) -> None:
        class Broken(Component):
            count: int = state(0)

            def render(self):
                return Row((Button("break", self.break_, "break"),))

            async def break_(self, event: PressEvent) -> None:
                self.count = 1
                raise RuntimeError("boom")

        seen: list[str] = []

        class Catch(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                try:
                    await proceed()
                except RuntimeError:
                    seen.append("caught")

        component = Broken()
        hook = AsyncMock()
        message_root = MessageRoot(component, access=Everyone(), middleware=(Catch(),), on_error=hook, timeout=None)
        commit_render(message_root)

        await message_root.dispatch("break", fake_interaction())

        assert seen == ["caught"]
        assert component.count == 0
        hook.assert_not_awaited()

    async def test_unhandled_middleware_error_reaches_the_message_root_error_hook(self) -> None:
        error = RuntimeError("policy service unavailable")

        class Fail(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                raise error

        hook = AsyncMock()
        message_root = MessageRoot(Counter(), access=Everyone(), middleware=(Fail(),), on_error=hook, timeout=None)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        hook.assert_awaited_once_with(interaction, error, "action:inc")

    async def test_proceed_is_one_shot_and_expires_after_dispatch(self) -> None:
        saved: list[ActionProceed] = []

        class SaveAndRepeat(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                saved.append(proceed)
                await proceed()
                await proceed()

        hook = AsyncMock()
        component = Counter()
        message_root = MessageRoot(
            component, access=Everyone(), middleware=(SaveAndRepeat(),), on_error=hook, timeout=None
        )
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction())

        assert component.count == 1
        assert hook.await_args is not None
        assert "may only be called once" in str(hook.await_args.args[1])
        with pytest.raises(RuntimeError, match="only valid during"):
            await saved[0]()

    async def test_request_marks_rebase_as_generation_metadata(self) -> None:
        requests: list[ActionRequest] = []

        class Capture(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                requests.append(request)
                await proceed()

        class Rebased(Component):
            current = False

            def render(self):
                handler = self.new if self.current else self.old
                return Row((Button("run", handler, "run", mode=ActionMode.REBASE),))

            async def old(self, event: PressEvent) -> None: ...

            async def new(self, event: PressEvent) -> None: ...

        component = Rebased()
        message_root = MessageRoot(component, access=Everyone(), middleware=(Capture(),), timeout=None)
        commit_render(message_root)
        submitted = message_root.generation
        component.current = True
        commit_render(message_root)
        active = message_root.generation

        await message_root.dispatch("run", fake_interaction(), generation=submitted)

        assert requests == [
            ActionRequest(
                requests[0].event,
                "run",
                InteractionKind.PRESS,
                ActionMode.REBASE,
                submitted,
                active,
                requests[0].context,
                rebased=True,
            )
        ]

    async def test_stale_exclusive_action_never_enters_middleware(self) -> None:
        entered = False

        class Capture(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                nonlocal entered
                entered = True
                await proceed()

        message_root = MessageRoot(Counter(), access=Everyone(), middleware=(Capture(),), timeout=None)
        commit_render(message_root)
        stale = message_root.generation
        commit_render(message_root)

        await message_root.dispatch("inc", fake_interaction(), generation=stale)

        assert not entered

    async def test_selection_and_submission_have_explicit_kinds(self) -> None:
        kinds: list[InteractionKind] = []

        class Capture(ActionMiddleware):
            async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
                kinds.append(request.kind)
                await proceed()

        class Picker(Component):
            def render(self):
                return SelectMenu((Option("A", "a"),), self.pick, "pick")

            async def pick(self, event: SelectionEvent) -> None: ...

        middleware = Capture()
        picker = MessageRoot(Picker(), access=Everyone(), middleware=(middleware,), timeout=None)
        commit_render(picker)
        await picker.dispatch("pick", fake_interaction(), ["a"])

        submit = AsyncMock()
        form_root = MessageRoot(Component(), access=Everyone(), middleware=(middleware,), timeout=None)
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))
        await form_root.dispatch_submit("rename", fake_interaction(), spec, {"name": "Ada"}, submit)

        assert kinds == [InteractionKind.SELECTION, InteractionKind.SUBMIT]


class TestErrors:
    async def test_handler_error_goes_to_hook(self):
        class Boom(Component):
            def render(self):
                return [Row((Button(label="x", on_click=self.explode, key="x"),))]

            async def explode(self, interaction) -> None:
                message = "boom"
                raise RuntimeError(message)

        hook = AsyncMock()
        message_root = MessageRoot(Boom(), access=Everyone(), timeout=None, on_error=hook)
        commit_render(message_root)

        await message_root.dispatch("x", fake_interaction())

        assert hook.await_args is not None
        (_interaction, error, source), _ = hook.await_args
        assert isinstance(error, RuntimeError)
        assert source == "action:x"

    async def test_a_field_parser_bug_reaches_the_error_hook(self):
        """A bug in `parse` is not a validation error, so it must not read as one."""

        @dataclass(frozen=True, slots=True)
        class Broken(FormField[str]):
            def parse(self, raw: object) -> str | None:
                return raw.no_such_attribute  # type: ignore[attr-defined]

        spec = FormSpec("Broken", (Broken(key="broken", label="Broken"),))
        hook = AsyncMock()
        message_root = MessageRoot(Component(), access=Everyone(), timeout=None, on_error=hook)

        await message_root.dispatch_submit("f", fake_interaction(), spec, {"broken": "x"}, AsyncMock())

        assert hook.await_args is not None
        (_interaction, error, source), _ = hook.await_args
        assert isinstance(error, AttributeError)
        assert source == "form:f"

    async def test_failed_handler_rolls_back_all_state_changes(self):
        class Boom(Component):
            count: int = state(0)
            entries: tuple[str, ...] = state(())

            def render(self):
                return [Row((Button(label="x", on_click=self.explode, key="x"),))]

            async def explode(self, interaction) -> None:
                self.count = 1
                self.entries = ("partial",)
                message = "boom"
                raise RuntimeError(message)

        component = Boom()
        hook = AsyncMock()
        message_root = MessageRoot(component, access=Everyone(), timeout=None, on_error=hook)
        commit_render(message_root)

        await message_root.dispatch("x", fake_interaction())

        assert component.count == 0
        assert component.entries == ()
        assert not message_root._dirty


class TestSelect:
    async def test_select_handler_receives_values(self):
        picked: list[str] = []

        class Picker(Component):
            def render(self):
                return [
                    SelectMenu(
                        options=(Option("A", "a"), Option("B", "b")),
                        on_select=self.pick,
                        key="pick",
                    )
                ]

            async def pick(self, event: SelectionEvent) -> None:
                picked.extend(event.values)

        message_root = MessageRoot(Picker(), access=Everyone(), timeout=None)
        view = commit_render(message_root)
        assert any(isinstance(item, discord.ui.Select) for item in view.walk_children())

        await message_root.dispatch("pick", fake_interaction(), ["b"])

        assert picked == ["b"]


class TestLifecycle:
    async def test_finish_disables_controls(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        await message_root.finish()

        disabled_view = message.edit.await_args.kwargs["view"]
        assert _button(disabled_view).disabled
        interaction = fake_interaction()
        await message_root.dispatch("inc", interaction)  # finished mounts ignore late clicks
        interaction.response.edit_message.assert_not_awaited()

    async def test_refresh_edits_bound_message(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))
        component.count = 7

        await message_root.refresh()

        message.edit.assert_awaited_once()

    async def test_reactor_coalesces_double_schedule(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message_root.refresh = AsyncMock()  # pyrefly: ignore
        scheduler = MessageRootScheduler()
        scheduler.schedule(message_root)
        scheduler.schedule(message_root)
        assert scheduler._queue.qsize() == 1

    async def test_expired_handle_marks_dirty_without_loading_or_staging(self):
        class Loaded(Component):
            def __init__(self) -> None:
                self.loads = 0

            async def on_load(self) -> None:
                self.loads += 1

            def render(self):
                return Text("loaded")

        component = Loaded()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(delivered_to(fake_message()))
        component._loaded = False
        message_root._handle = delivery.handle_from(fake_interaction(expired=True))
        issued = message_root._issued

        await message_root.refresh()

        assert component.loads == 1
        assert message_root._issued == issued
        assert message_root.pending

    async def test_accepted_click_clears_status_and_flushes_without_it(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(fake_message()))
        message_root.status = "Live updates paused"
        message_root.invalidate()
        interaction = fake_interaction()

        await message_root.dispatch("inc", interaction)

        written = interaction.response.edit_message.await_args.kwargs["view"]
        assert "Live updates paused" not in str(written.to_components())
        assert message_root.status is None

    async def test_background_refreshes_preserve_the_interaction_idle_budget(self):
        now = 100.0
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=30, clock=lambda: now)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        for elapsed in range(1, 11):
            now = 100.0 + elapsed
            await message_root.refresh()

        message.edit.assert_not_awaited()
        assert message_root.snapshot().idle == 10
        assert message_root.snapshot().expires_in == 20


class TestDeliveryAtomicity:
    """A render becomes the mount's state only once Discord has accepted it."""

    def test_stage_view_stages_without_committing(self):
        """The stage-only escape hatch renders the tree and publishes none of it.

        Committing is `send`'s and `flush`'s job; `TestSend` covers the other half.
        """
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)

        message_root._stage_view()

        assert message_root._handlers == {}
        assert message_root._generation == 0
        assert message_root._assets == ()

    async def test_failed_edit_keeps_the_visible_generation_live(self, monkeypatch):
        mounted: list[str] = []
        panel = Panel(mounted)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("__cursor_next.entries", fake_interaction())
        assert message_root.presentation.cursor("entries").position.offset == 1

        live_generation = message_root._generation
        live_handlers = message_root._handlers
        live_strategies = dict(message_root.presentation.strategies)
        panel.entries = (*panel.entries, "entry 6")  # a new fingerprint: the staged render resets the cursor
        panel.show_child = True  # a component the failed generation must not mount

        monkeypatch.setattr(delivery, "handle_from", _refuse_handle)
        with pytest.raises(discord.HTTPException):
            await message_root.refresh(fake_interaction())

        assert message_root._generation == live_generation
        assert message_root._handlers is live_handlers
        assert message_root._dirty
        assert mounted == []
        assert message_root.presentation.cursor("entries").position.offset == 1
        # Planning only reads the session, so a discarded candidate leaves behind none of
        # its writes — not just the cursors the old snapshot happened to restore.
        assert message_root.presentation.strategies == live_strategies

    async def test_a_click_after_a_failed_edit_still_runs_and_repairs_the_message(self, monkeypatch):
        mounted: list[str] = []
        panel = Panel(mounted)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        commit_render(message_root)
        live_generation = message_root._generation
        panel.show_child = True

        monkeypatch.setattr(delivery, "handle_from", _refuse_handle)
        with pytest.raises(discord.HTTPException):
            await message_root.refresh(fake_interaction())
        monkeypatch.undo()

        # The stale-generation guard would silently defer this click if the mount had
        # advanced past the generation the message is still showing.
        interaction = fake_interaction()
        await message_root.dispatch("add", interaction, generation=live_generation)

        assert panel.entries[-1] == "added"
        assert message_root._generation > live_generation
        assert not message_root._dirty
        assert mounted == ["child"]
        interaction.response.edit_message.assert_awaited_once()

    async def test_failed_refresh_leaves_the_message_root_repairable(self, monkeypatch):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        message.edit = AsyncMock(side_effect=_http_error())
        await message_root.send(delivered_to(message))
        component.count = 7
        live_generation = message_root._generation

        with pytest.raises(discord.HTTPException):
            await message_root.refresh()

        assert message_root._generation == live_generation
        assert message_root._dirty

        message.edit = AsyncMock(return_value=message)
        await message_root.refresh()

        assert message_root._generation > live_generation
        assert not message_root._dirty

    async def test_refresh_commit_preserves_invalidation_during_delivery(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        started = asyncio.Event()
        release = asyncio.Event()

        async def edit(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await release.wait()
            return message

        message.edit = AsyncMock(side_effect=edit)
        component.count = 1

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.refresh)
            await started.wait()
            component.count = 2
            release.set()

        assert message_root.generation == 2
        assert message_root.pending
        assert message_root.runtime.dirty

    async def test_two_refreshes_deliver_in_generation_order(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        started = asyncio.Event()
        release = asyncio.Event()
        second_started = asyncio.Event()
        writes: list[discord.ui.LayoutView] = []

        async def edit(view: discord.ui.LayoutView, **kwargs: Any) -> Any:
            writes.append(view)
            if len(writes) == 1:
                started.set()
                await release.wait()
            else:
                second_started.set()
            return message

        message.edit = AsyncMock(side_effect=edit)
        component.count = 1

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.refresh)
            await started.wait()
            component.count = 2
            interaction = fake_interaction()
            interaction.response.edit_message = AsyncMock(side_effect=edit)
            tasks.start_soon(message_root.refresh, interaction)
            await anyio.sleep(0)
            assert not second_started.is_set()
            release.set()
            await second_started.wait()

        assert len(writes) == 2
        assert "count: 1" in str(writes[0].to_components())
        assert "count: 2" in str(writes[1].to_components())
        assert message_root.generation == 3
        assert not message_root.pending

    async def test_concurrent_immediate_actions_serialize_delivery(self):
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum_active = 0
        entered = 0

        class ImmediatePanel(Component):
            count: int = state(0)

            def render(self):
                return Row(
                    (
                        Button(f"a:{self.count}", self.click, "a", mode=ActionMode.IMMEDIATE),
                        Button(f"b:{self.count}", self.click, "b", mode=ActionMode.IMMEDIATE),
                    )
                )

            async def click(self, event: PressEvent) -> None:
                nonlocal entered
                entered += 1
                if entered == 2:
                    started.set()
                await release.wait()
                self.count += 1

        component = ImmediatePanel()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        async def edit(*args: Any, **kwargs: Any) -> Any:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await anyio.sleep(0)
            active -= 1
            return message

        first = fake_interaction()
        second = fake_interaction()
        first.response.edit_message = AsyncMock(side_effect=edit)
        second.response.edit_message = AsyncMock(side_effect=edit)

        async def dispatch_first() -> None:
            await message_root.dispatch("a", first)

        async def dispatch_second() -> None:
            await message_root.dispatch("b", second)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(dispatch_first)
            tasks.start_soon(dispatch_second)
            await started.wait()
            release.set()

        assert component.count == 2
        assert maximum_active == 1
        assert not message_root.pending

    async def test_finish_waits_for_an_in_flight_refresh(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(delivered_to(message))

        started = asyncio.Event()
        release = asyncio.Event()
        writes: list[discord.ui.LayoutView] = []

        async def edit(view: discord.ui.LayoutView, **kwargs: Any) -> Any:
            writes.append(view)
            if len(writes) == 1:
                started.set()
                await release.wait()
            return message

        message.edit = AsyncMock(side_effect=edit)
        component.count = 1

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.refresh)
            await started.wait()
            tasks.start_soon(message_root.finish)
            await anyio.sleep(0)
            assert not message_root.finished
            release.set()

        assert message_root.finished
        assert len(writes) == 2
        assert all(item.disabled for item in writes[1].walk_children() if isinstance(item, discord.ui.Button))


class _Destination:
    """A recording destination. `message` is whatever its result exposes to the mount."""

    def __init__(
        self,
        message: Any = None,
        *,
        handle: delivery.EditHandle | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.message = message
        self.handle = delivery.handle_for(message) if message is not None and handle is None else handle
        self.raises = raises
        self.calls: list[tuple[discord.ui.LayoutView, list[discord.File]]] = []

    async def __call__(self, presentation: squid_ui_discord.presentation.DiscordPresentation) -> Any:
        self.calls.append((presentation.layout, presentation.build_files()))
        if self.raises is not None:
            raise self.raises
        return delivery.DeliveryResult(self.message, self.handle)


class Report(Component):
    """A component carrying one inline asset, so a send has files to hand over."""

    def render(self):
        return Document(
            (Text("summary"),),
            (Asset("report", "report.txt", "text/plain", InlineAsset(b"full report")),),
        )


class MutableReport(Component):
    def __init__(self) -> None:
        self.contents = b"first"

    def render(self):
        return Document(
            (Text("summary"),),
            (Asset("report", "report.txt", "text/plain", InlineAsset(self.contents)),),
        )


class TestSend:
    """`MessageRoot.send` runs stage -> deliver -> commit; the destination only says where."""

    async def test_a_successful_send_commits_and_keeps_the_message_handle(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        message = fake_message()
        destination = _Destination(message)

        sent = await message_root.send(destination)

        assert isinstance(sent, delivery.Delivered)
        assert sent.settled
        assert sent.result.message is message
        assert "inc" in message_root._handlers
        assert message_root._generation == 1
        assert not message_root.pending
        assert message_root.handle is not None
        assert message_root.handle.permanent

    async def test_send_and_refresh_share_render_delivery_spans(self) -> None:
        profiler = MemoryProfiler()
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), profiler=profiler, timeout=None)
        message = fake_message()

        await message_root.send(delivered_to(message))

        sent = _operation_trace(profiler, OperationKind.SEND)
        assert sent.result.presentation is PresentationStatus.WRITTEN
        assert {span.name for span in sent.spans} >= {
            "render_lock",
            "runtime_render",
            "planner",
            "renderer",
            "discord_write",
            "commit",
        }

        component.count = 4
        await message_root.refresh()

        refreshed = _operation_trace(profiler, OperationKind.REFRESH)
        assert refreshed.result.presentation is PresentationStatus.WRITTEN
        assert {span.name for span in refreshed.spans} >= {
            "render_lock",
            "runtime_render",
            "planner",
            "renderer",
            "discord_write",
            "commit",
        }

    async def test_a_successful_send_keeps_the_receipts_handle_without_reconstructing_it(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        message = fake_message()
        authority = _RefusingHandle()

        await message_root.send(_Destination(message, handle=authority))

        assert message_root.handle is authority

    async def test_a_destination_with_no_message_commits_and_waits_for_the_first_click(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        sent = await message_root.send(_Destination(None))

        # Delivered, so the render is live -- but nothing came back to write through.
        assert isinstance(sent, delivery.Delivered)
        assert sent.result.message is None
        assert message_root._generation == 1
        assert not message_root.pending
        assert message_root.handle is None

        # The first click renews the mount, exactly as an ephemeral send relies on.
        await message_root.dispatch("inc", fake_interaction())

        assert component.count == 1
        assert message_root.handle is not None

    async def test_handleless_operation_settles_without_repainting(self) -> None:
        panel = OperationPanel()
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)

        sent = await message_root.send(_Destination(None))

        assert isinstance(sent, delivery.Delivered)
        assert sent.settled
        assert panel.publication.status == sl.operations.Succeeded(42)

    async def test_dismiss_deletes_the_message_and_finishes_the_root(self) -> None:
        message = fake_message()
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(message))

        await message_root.dismiss()

        message.delete.assert_awaited_once_with()
        assert message_root.finished

    async def test_an_abandoned_delivery_leaves_the_message_root_resendable(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        abandoned = _Destination(raises=delivery.DeliveryAbandoned())

        sent = await message_root.send(abandoned)

        # Nothing reached Discord, so nothing is live: no handlers, no handle, still dirty.
        assert isinstance(sent, delivery.Abandoned)
        assert message_root._generation == 0
        assert message_root._handlers == {}
        assert message_root.handle is None
        assert message_root.pending

        message = fake_message()
        resent = await message_root.send(_Destination(message))
        assert isinstance(resent, delivery.Delivered)
        assert resent.result.message is message
        # Generation 2, not 1: the abandoned candidate does not hand its control ids on.
        assert message_root._generation == 2
        assert not message_root.pending

    async def test_a_failed_delivery_propagates_and_the_next_send_recovers(self):
        mounted: list[str] = []
        panel = Panel(mounted)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        panel.show_child = True

        with pytest.raises(discord.HTTPException):
            await message_root.send(_Destination(raises=_http_error()))

        assert message_root._generation == 0
        assert message_root._handlers == {}
        assert message_root.pending
        # A candidate that was never delivered must not fire its lifecycle hooks.
        assert mounted == []

        await message_root.send(_Destination(fake_message()))

        assert message_root._generation > 0
        assert not message_root.pending
        assert mounted == ["child"]

    async def test_the_staged_assets_reach_the_destination(self):
        message_root = MessageRoot(Report(), access=Everyone(), timeout=None)
        destination = _Destination(fake_message())

        await message_root.send(destination)

        _, files = destination.calls[0]
        assert [file.filename for file in files] == ["report.txt"]

    async def test_changed_asset_content_prevents_scene_suppression(self) -> None:
        component = MutableReport()
        message: Any = fake_message()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(_Destination(message))
        component.contents = b"second"

        result = await message_root.refresh()

        assert result is PresentationStatus.WRITTEN
        message.edit.assert_awaited_once()

    async def test_changed_handler_identity_is_published_without_repainting(self) -> None:
        class FreshHandler(Component):
            version = 0
            invoked: list[int] = []

            def render(self):
                version = self.version

                async def click(event: PressEvent) -> None:
                    self.invoked.append(version)

                return Row((Button("same", click, "same"),))

        message: Any = fake_message()
        component = FreshHandler()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(_Destination(message))
        generation = message_root._generation
        view = message_root._view
        component.version = 1
        message_root.invalidate()

        result = await message_root.refresh()

        assert result is PresentationStatus.UNCHANGED
        message.edit.assert_not_awaited()
        assert message_root._generation == generation
        assert message_root._view is view

        await message_root.dispatch("same", fake_interaction(), generation=generation)

        assert component.invoked == [1]

    async def test_identical_refresh_is_suppressed_before_renderer_or_generation(self, monkeypatch) -> None:
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        message: Any = fake_message()
        await message_root.send(_Destination(message))
        issued = message_root._issued

        def unexpected_renderer(_timeout):
            message = "an identical refresh must not construct a renderer"
            raise AssertionError(message)

        monkeypatch.setattr(message_root, "_renderer", unexpected_renderer)

        assert await message_root.refresh() is PresentationStatus.UNCHANGED
        assert message_root._issued == issued
        assert message.edit.await_count == 0

    async def test_suppression_publishes_runtime_only_action_semantics(self) -> None:
        class Guarded(Component):
            allowed = True
            invoked = 0

            async def click(self, event: ActionEvent) -> None:
                self.invoked += 1

            def render(self):
                return sl.action_controls(
                    sl.action_control(
                        "same",
                        self.click,
                        key="same",
                        guard=sl.guards.when(lambda event: self.allowed, reason="Closed."),
                    ),
                    key="guarded",
                )

        component = Guarded()
        message: Any = fake_message()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(_Destination(message))
        component.allowed = False
        message_root.invalidate()

        assert await message_root.refresh() is PresentationStatus.UNCHANGED

        interaction = fake_interaction()
        await message_root.dispatch("same", interaction, generation=message_root._generation)

        assert component.invoked == 0
        interaction.response.send_message.assert_awaited_once()

    async def test_send_supersedes_a_render_that_was_only_staged(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        staged = message_root._stage_view()
        component.count = 5

        destination = _Destination(fake_message())
        await message_root.send(destination)

        delivered, _ = destination.calls[0]
        assert delivered is not staged
        assert staged.is_finished()
        assert message_root._pending is None
        # The delivered generation is the one the mount is now live on.
        assert message_root._view is delivered

    async def test_a_finished_message_root_does_not_send(self):
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        await message_root.finish(disable=False)
        destination = _Destination(fake_message())

        assert isinstance(await message_root.send(destination), delivery.Abandoned)
        assert destination.calls == []


class TestStateDescriptor:
    def test_default_is_per_instance(self):
        first, second = Counter(), Counter()
        first.count = 5
        assert second.count == 0

    def test_assignment_marks_message_root_dirty(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        commit_render(message_root)
        assert not message_root._dirty
        component.count = 3
        assert message_root._dirty

    def test_a_factory_runs_once_per_instance(self):
        class Collection(Component):
            entries: tuple[str, ...] = state(factory=tuple)

            def render(self):
                return Text(str(self.entries))

        first, second = Collection(), Collection()
        message_root = MessageRoot(first, access=Everyone(), timeout=None)
        commit_render(message_root)

        first.entries = ("one",)

        assert message_root._dirty
        assert second.entries == ()

    def test_computed_values_cache_until_state_changes(self):
        class Derived(Component):
            count: int = state(1)
            unrelated: int = state(0)

            def __init__(self) -> None:
                self.calls = 0

            @computed
            def doubled(self) -> int:
                self.calls += 1
                return self.count * 2

            def render(self):
                return Text(str(self.doubled))

        component = Derived()
        assert component.doubled == 2
        assert component.doubled == 2
        assert component.calls == 1

        component.unrelated = 1
        assert component.doubled == 2
        assert component.calls == 1

        component.count = 3
        assert component.doubled == 6
        assert component.calls == 2

    def test_a_computed_may_read_another_component_s_state(self):
        """Tracking has no same-component rule to enforce: a read is a read."""

        class Source(Component):
            count: int = state(1)

            def render(self):
                return Text(str(self.count))

        class Reader(Component):
            def __init__(self, source: Source) -> None:
                self.source = source

            @computed
            def doubled(self) -> int:
                return self.source.count * 2

            def render(self):
                return Text(str(self.doubled))

        source = Source()
        reader = Reader(source)
        assert reader.doubled == 2

        source.count = 5

        assert reader.doubled == 10

    def test_computed_values_propagate_only_when_their_value_changes(self):
        class Derived(Component):
            query: str = state("FIRST")

            def __init__(self) -> None:
                self.calls = 0

            @computed
            def normalized(self) -> str:
                return self.query.casefold()

            @computed
            def label(self) -> str:
                self.calls += 1
                return f"query:{self.normalized}"

            def render(self):
                return Text(self.label)

        component = Derived()
        assert component.label == "query:first"
        assert component.calls == 1

        component.query = "first"
        assert component.label == "query:first"
        assert component.calls == 1

        component.query = "second"
        assert component.label == "query:second"
        assert component.calls == 2

    def test_a_computed_that_reads_itself_says_so(self):
        """A tracked cycle is only visible when it runs, so that is where it is reported."""

        class Cyclic(Component):
            @computed
            def first(self) -> int:
                return self.second

            @computed
            def second(self) -> int:
                return self.first

            def render(self):
                return Text("")

        # Neither computed reads itself; the pair is the mistake, so the ring is what is named.
        with pytest.raises(sl.runtime.ReactiveCycleError) as raised:
            _ = Cyclic().first
        assert raised.value.path == ("Cyclic.first", "Cyclic.second", "Cyclic.first")

    def test_batch_coalesces_invalidations(self):
        class Pair(Component):
            left: int = state(0)
            right: int = state(0)

            def __init__(self) -> None:
                self.invalidations = 0

            def invalidate(self) -> None:
                self.invalidations += 1
                super().invalidate()

            def render(self):
                return Text(f"{self.left}:{self.right}")

        component = Pair()
        with batch():
            component.left = 1
            component.right = 2

        assert component.invalidations == 1

    def test_transaction_rolls_back_every_assignment_it_covered(self):
        class Form(Component):
            name: str = state("before")
            values: tuple[int, ...] = state(())

            def render(self):
                return Text(self.name)

        component = Form()
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.name = "after"
            component.values = (1,)
            raise RuntimeError("abort")

        assert component.name == "before"
        assert component.values == ()


class Notifier(Component):
    """Writes state and then answers with a notice — the shape that broke the panel."""

    count: int = state(0)

    def render(self):
        return [Text(f"count: {self.count}"), Row((Button(label="go", on_click=self.go, key="go"),))]

    async def go(self, event: PressEvent) -> None:
        self.count += 1
        await event.notice("heads up")


class TestEditHandles:
    def test_handle_from_refuses_an_interaction_spent_on_another_message(self):
        spent = fake_interaction()
        spent.response.type = discord.InteractionResponseType.channel_message
        assert delivery.handle_from(spent) is None

        modal = fake_interaction()
        modal.response.type = discord.InteractionResponseType.modal
        assert delivery.handle_from(modal) is None

    def test_handle_from_accepts_an_unspent_or_update_shaped_interaction(self):
        assert delivery.handle_from(fake_interaction()) is not None

        deferred = fake_interaction()
        deferred.response.type = discord.InteractionResponseType.deferred_message_update
        handle = delivery.handle_from(deferred)
        assert handle is not None
        assert not handle.permanent
        assert handle.expires_at == deferred.expires_at

    def test_channel_message_handle_is_permanent_regardless_of_message_flags(self):
        assert delivery.handle_for(fake_message()).permanent
        assert delivery.handle_for(fake_message(ephemeral=True)).permanent

    async def test_interaction_message_edit_is_pinned_to_the_original_response_endpoint(self):
        expected = object()
        interaction = SimpleNamespace(edit_original_response=AsyncMock(return_value=expected))
        subject = cast(
            discord.InteractionMessage,
            SimpleNamespace(_state=SimpleNamespace(_interaction=interaction), delete=AsyncMock()),
        )

        edited = await discord.InteractionMessage.edit(subject, content="updated")

        assert edited is expected
        interaction.edit_original_response.assert_awaited_once_with(
            content="updated",
            embeds=discord.utils.MISSING,
            embed=discord.utils.MISSING,
            attachments=discord.utils.MISSING,
            view=discord.utils.MISSING,
            allowed_mentions=None,
            poll=discord.utils.MISSING,
        )

    async def test_webhook_message_edit_is_pinned_to_the_webhook_message_endpoint(self):
        expected = object()
        webhook = SimpleNamespace(edit_message=AsyncMock(return_value=expected))
        subject = cast(
            discord.WebhookMessage,
            SimpleNamespace(
                id=42,
                _state=SimpleNamespace(_webhook=webhook, _thread=discord.utils.MISSING),
            ),
        )

        edited = await discord.WebhookMessage.edit(subject, content="updated")

        assert edited is expected
        webhook.edit_message.assert_awaited_once_with(
            42,
            content="updated",
            embeds=discord.utils.MISSING,
            embed=discord.utils.MISSING,
            attachments=discord.utils.MISSING,
            view=discord.utils.MISSING,
            allowed_mentions=None,
            thread=discord.utils.MISSING,
        )

    async def test_application_webhook_followups_force_wait_and_return_the_message(self):
        expected = object()
        adapter = SimpleNamespace(execute_webhook=AsyncMock(return_value={}))
        subject = cast(
            discord.Webhook,
            SimpleNamespace(
                type=discord.WebhookType.application,
                token="interaction-token",
                id=42,
                _state=SimpleNamespace(allowed_mentions=None),
                session=object(),
                proxy=None,
                proxy_auth=None,
                _create_message=lambda data, *, thread: expected,
            ),
        )
        token = async_context.set(cast(AsyncWebhookAdapter, adapter))
        try:
            sent = await discord.Webhook.send(subject, wait=False)
        finally:
            async_context.reset(token)

        assert sent is expected
        assert adapter.execute_webhook.await_args.kwargs["wait"] is True

    async def test_a_notice_does_not_swallow_the_render_it_came_with(self):
        # `notice` answers with a new message, which moves the interaction's original
        # response off the panel. Editing through it would overwrite the notice and leave
        # the panel stale, so the mount falls back to the message it holds.
        message = fake_message()
        message_root = MessageRoot(Notifier(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(message))

        interaction = fake_interaction()
        await message_root.dispatch("go", interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.edit_message.assert_not_awaited()
        message.edit.assert_awaited_once()
        assert not message_root.pending

    async def test_a_flush_through_the_standing_handle_still_answers_the_click(self):
        # A modal submitted from a command rather than from a component carries no message,
        # so `handle_from` has nothing to build on and the edit goes through the mount's own
        # handle. Only the interaction's handle answers the click by editing through it, so
        # the flush owes an acknowledgement -- without one Discord reports a failure at 3s.
        message = fake_message()
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(delivered_to(message))

        component.count = 3
        interaction = fake_interaction()
        interaction.message = None

        await message_root.refresh(interaction)

        message.edit.assert_awaited_once()
        interaction.response.defer.assert_awaited_once()
        assert not message_root.pending

    async def test_a_click_renews_an_ephemeral_message_root_for_background_refreshes(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        initial = fake_interaction()
        await message_root.send(delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(initial)))
        assert message_root.handle is not None and not message_root.handle.permanent

        interaction = fake_interaction()
        await message_root.dispatch("inc", interaction)
        assert message_root.handle is not None
        assert message_root.handle.expires_at == interaction.expires_at

        component.count += 1
        await message_root.refresh()

        interaction.followup.edit_message.assert_awaited_once()
        assert interaction.followup.edit_message.await_args.args[0] == interaction.message.id
        assert not message_root.pending

    async def test_a_click_does_not_trade_away_the_bots_own_credentials(self):
        message = fake_message()
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(message))
        permanent = message_root.handle

        await message_root.dispatch("inc", fake_interaction())

        assert message_root.handle is permanent

    async def test_an_unreachable_message_root_holds_its_render_for_the_next_click(self):
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(
            delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(fake_interaction()))
        )
        message_root._handle = delivery.handle_from(fake_interaction(expired=True))
        component.count += 1

        await message_root.refresh()

        # Not an error and not the end of the mount: the message is simply out of reach
        # until someone clicks it again.
        assert message_root.pending
        assert not message_root._finished

        interaction = fake_interaction()
        await message_root.dispatch("inc", interaction)

        interaction.response.edit_message.assert_awaited_once()
        assert not message_root.pending
        assert component.count == 2

    async def test_a_stale_handle_is_dropped_rather_than_reused(self):
        class _Stale:
            permanent = False
            expires_at = None
            mode = squid_ui_discord.DiscordMode.COMPONENTS_V2
            writes = 0

            def expired(self) -> bool:
                return False

            async def write(self, *args: Any, **kwargs: Any) -> None:
                type(self).writes += 1
                raise delivery.StaleHandleError("gone")

        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(
            delivered_to(fake_message(ephemeral=True), handle=delivery.handle_from(fake_interaction()))
        )
        message_root._handle = _Stale()
        component.count += 1

        await message_root.refresh()
        await message_root.refresh()

        assert _Stale.writes == 1
        assert message_root.handle is None
        assert message_root.pending


class TestDestinations:
    async def test_fresh_unwaited_response_commits_an_original_response_handle_without_fetching(self):
        interaction = fake_interaction()
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        sent = await message_root.send(delivery.respond_to(interaction, wait=False))

        assert isinstance(sent, delivery.Delivered)
        assert sent.result.message is None
        assert message_root.handle is not None and not message_root.handle.permanent
        assert message_root.handle.expires_at == interaction.expires_at
        interaction.original_response.assert_not_awaited()

        component.count += 1
        await message_root.refresh()

        interaction.edit_original_response.assert_awaited_once()
        assert not message_root.pending

    async def test_fresh_waited_public_response_keeps_token_authority_not_message_authority(self):
        interaction = fake_interaction()
        message = fake_message(ephemeral=False)
        interaction.original_response.return_value = message
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        sent = await message_root.send(delivery.respond_to(interaction, ephemeral=False, wait=True))

        assert isinstance(sent, delivery.Delivered)
        assert sent.result.message is message
        assert message_root.handle is not None and not message_root.handle.permanent
        assert message_root.handle.expires_at == interaction.expires_at

        component.count += 1
        await message_root.refresh()

        interaction.edit_original_response.assert_awaited_once()
        message.edit.assert_not_awaited()

    async def test_waited_followup_keeps_webhook_message_authority(self):
        interaction = fake_interaction()
        interaction.response._done = True
        message = fake_message(message_id=42)
        interaction.followup.send.return_value = message
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        await message_root.send(delivery.respond_to(interaction, wait=True))
        component.count += 1
        await message_root.refresh()

        interaction.followup.edit_message.assert_awaited_once()
        assert interaction.followup.edit_message.await_args.args[0] == 42
        message.edit.assert_not_awaited()

    async def test_followup_exposes_the_message_and_handle_even_when_wait_was_not_requested(self):
        interaction = fake_interaction()
        interaction.response._done = True
        message = fake_message(message_id=42)
        interaction.followup.send.return_value = message
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)

        sent = await message_root.send(delivery.respond_to(interaction, wait=False))

        assert isinstance(sent, delivery.Delivered)
        assert sent.result.message is message
        assert message_root.handle is not None
        assert not message_root.handle.permanent

    async def test_plain_command_reply_keeps_permanent_channel_authority(self):
        message = fake_message()
        ctx = cast(delivery.Replyable, SimpleNamespace(send=AsyncMock(return_value=message)))
        message_root = MessageRoot(Counter(), access=Everyone(), timeout=None)

        await message_root.send(delivery.reply_to(ctx))

        assert message_root.handle is not None and message_root.handle.permanent

    async def test_interaction_backed_context_reply_keeps_original_response_authority(self):
        interaction = fake_interaction()
        message = fake_message()
        ctx = cast(
            delivery.Replyable,
            SimpleNamespace(interaction=interaction, send=AsyncMock(return_value=message)),
        )
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        await message_root.send(delivery.reply_to(ctx))

        assert message_root.handle is not None and not message_root.handle.permanent
        component.count += 1
        await message_root.refresh()
        interaction.edit_original_response.assert_awaited_once()

    async def test_stale_public_response_drops_then_renews_for_the_pending_render(self):
        interaction = fake_interaction()
        interaction.original_response.return_value = fake_message(ephemeral=False)
        interaction.edit_original_response.side_effect = _stale_http_error()
        component = Counter()
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(delivery.respond_to(interaction, ephemeral=False, wait=True))
        component.count += 1

        await message_root.refresh()

        assert message_root.handle is None
        assert message_root.pending

        click = fake_interaction()
        await message_root.dispatch("inc", click)

        assert message_root.handle is not None
        assert not message_root.pending
        click.response.edit_message.assert_awaited_once()


class VisibleResourcePanel(Component):
    key: str = state("first")

    def __init__(self, load: Callable[[str], Awaitable[str]]) -> None:
        self._load = load

    @resource
    async def value(self) -> str:
        return await self._load(self.key)

    async def change(self, event: PressEvent) -> None:
        self.key = "second"

    def render(self):
        match self.value.status:
            case Pending(previous=previous):
                label = "pending" if previous is None else f"pending:{previous.value}"
            case Failed(error=error):
                label = f"failed:{error}"
            case Ready(value=value):
                label = f"ready:{value}"
        return [Text(label), Row((Button("change", self.change, "change"),))]


class AtomicResourcePanel(Component):
    def __init__(self, load: Callable[[], Awaitable[str]]) -> None:
        self._load = load

    @resource(pending=PendingMode.ATOMIC)
    async def value(self) -> str:
        return await self._load()

    def render(self):
        match self.value.status:
            case Failed(error=error):
                return Text(f"failed:{error}")
            case Ready(value=value):
                return Text(f"ready:{value}")


class CheckpointedResourcePanel(Component):
    """A panel whose first load parks at a checkpoint, so it can be superseded mid-settle."""

    def __init__(self) -> None:
        self.attempts = 0
        self.finished: list[int] = []
        self.entered = asyncio.Event()
        self.released = asyncio.Event()

    @resource(pending=PendingMode.ATOMIC)
    async def value(self) -> str:
        self.attempts += 1
        attempt = self.attempts
        if attempt == 1:
            self.entered.set()
            await self.released.wait()
        self.finished.append(attempt)
        return f"attempt-{attempt}"

    def render(self):
        match self.value.status:
            case Failed(error=error):
                return Text(f"failed:{error}")
            case Ready(value=value):
                return Text(f"ready:{value}")


class OperationPanel(Component):
    def __init__(self) -> None:
        self.publication = self._publication.start()

    @sl.operation(initial="starting")
    async def _publication(self, progress: sl.operations.ProgressReporter[str]) -> int:
        progress.report("publishing")
        return 42

    def render(self):
        match self.publication.status:
            case sl.operations.Pending(progress=progress):
                return Text(f"pending:{progress}")
            case sl.operations.Succeeded(value=value):
                return Text(f"succeeded:{value}")
            case sl.operations.Failed(error=error):
                return Text(f"failed:{error}")
            case sl.operations.Cancelled(progress=progress):
                return Text(f"cancelled:{progress}")


class ProgressiveOperationPanel(OperationPanel):
    def __init__(self, progressed: asyncio.Event, resume: asyncio.Event) -> None:
        self.progressed = progressed
        self.resume = resume
        super().__init__()

    @sl.operation(initial="starting")
    async def _publication(self, progress: sl.operations.ProgressReporter[str]) -> int:
        progress.report("publishing")
        self.progressed.set()
        await self.resume.wait()
        return 42


class TestResourceLoading:
    async def test_operation_delivers_pending_then_succeeded(self) -> None:
        message: Any = fake_message()
        destination = _Destination(message)
        message_root = MessageRoot(OperationPanel(), access=Everyone(), timeout=None)

        await message_root.send(destination)

        assert "pending:starting" in str(destination.calls[0][0].to_components())
        message.edit.assert_awaited_once()
        assert "succeeded:42" in str(message.edit.await_args.kwargs["view"].to_components())

    async def test_operation_progress_reconciles_while_it_is_running(self) -> None:
        progressed = asyncio.Event()
        resume = asyncio.Event()
        painted = asyncio.Event()
        message: Any = fake_message()

        def record_edit(**_kwargs: object) -> object:
            painted.set()
            return message

        message.edit.side_effect = record_edit
        message_root = MessageRoot(ProgressiveOperationPanel(progressed, resume), access=Everyone(), timeout=None)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(message_root.send, _Destination(message))
            await progressed.wait()
            await painted.wait()
            assert "pending:publishing" in str(message.edit.await_args_list[0].kwargs["view"].to_components())
            resume.set()

        assert message.edit.await_count == 2
        assert "succeeded:42" in str(message.edit.await_args_list[1].kwargs["view"].to_components())

    async def test_visible_resource_delivers_pending_then_ready(self) -> None:
        async def load(_key: str) -> str:
            return "loaded"

        panel = VisibleResourcePanel(load)
        message: Any = fake_message()
        destination = _Destination(message)
        profiler = MemoryProfiler()
        message_root = MessageRoot(panel, access=Everyone(), profiler=profiler, timeout=None)

        await message_root.send(destination)

        assert len(destination.calls) == 1
        assert "pending" in str(destination.calls[0][0].to_components())
        message.edit.assert_awaited_once()
        assert "ready:loaded" in str(message.edit.await_args.kwargs["view"].to_components())
        assert not message_root.pending
        trace = _operation_trace(profiler, OperationKind.SEND)
        assert "resource_settle.visible" in {span.name for span in trace.spans}

    async def test_visible_resource_suppresses_an_identical_settled_scene(self) -> None:
        class UnprojectedResource(Component):
            @resource
            async def value(self) -> str:
                return "loaded"

            def render(self):
                _ = self.value.status
                return Text("constant")

        message: Any = fake_message()
        message_root = MessageRoot(UnprojectedResource(), access=Everyone(), timeout=None)

        await message_root.send(_Destination(message))

        message.edit.assert_not_awaited()
        assert message_root.snapshot().suppressed == 1
        assert not message_root.pending

    async def test_atomic_resource_delivers_only_the_settled_render(self) -> None:
        async def load() -> str:
            return "loaded"

        message: Any = fake_message()
        destination = _Destination(message)
        profiler = MemoryProfiler()
        message_root = MessageRoot(AtomicResourcePanel(load), access=Everyone(), profiler=profiler, timeout=None)

        await message_root.send(destination)

        assert len(destination.calls) == 1
        assert "ready:loaded" in str(destination.calls[0][0].to_components())
        message.edit.assert_not_awaited()
        trace = _operation_trace(profiler, OperationKind.SEND)
        assert "resource_settle.atomic" in {span.name for span in trace.spans}

    async def test_atomic_state_excludes_pending_and_keeps_previous_ready_value(self) -> None:
        async def load() -> str:
            return "loaded"

        panel = AtomicResourcePanel(load)
        with pytest.raises(sl.resources.ResourceNotReadyError, match=r"atomic resource .* pending"):
            _ = panel.value.status

        await panel.value.reload()
        assert panel.value.status == Ready("loaded")

        panel.value.invalidate()
        assert panel.value.pending
        assert panel.value.status == Ready("loaded")

    async def test_visible_failure_is_rendered_as_state(self) -> None:
        async def load(_key: str) -> str:
            message = "offline"
            raise RuntimeError(message)

        message: Any = fake_message()
        message_root = MessageRoot(VisibleResourcePanel(load), access=Everyone(), timeout=None)

        await message_root.send(_Destination(message))

        message.edit.assert_awaited_once()
        assert "failed:offline" in str(message.edit.await_args.kwargs["view"].to_components())
        assert not message_root.pending

    async def test_visible_siblings_load_concurrently(self) -> None:
        started = anyio.Event()

        class Pair(Component):
            @resource
            async def first(self) -> str:
                started.set()
                return "first"

            @resource
            async def second(self) -> str:
                await started.wait()
                return "second"

            def render(self):
                return Text(f"{type(self.first.status).__name__}:{type(self.second.status).__name__}")

        message: Any = fake_message()
        message_root = MessageRoot(Pair(), access=Everyone(), timeout=None)

        with anyio.fail_after(5):
            await message_root.send(_Destination(message))

        message.edit.assert_awaited_once()
        assert "Ready:Ready" in str(message.edit.await_args.kwargs["view"].to_components())

    async def test_settlement_loads_a_newly_revealed_nested_resource(self) -> None:
        loads: list[str] = []

        class Child(Component):
            @resource
            async def value(self) -> str:
                loads.append("child")
                return "child loaded"

            def render(self):
                return Text(f"child:{type(self.value.status).__name__}")

        class Parent(Component):
            def __init__(self) -> None:
                self.child = Child()

            @resource
            async def value(self) -> str:
                loads.append("parent")
                return "parent loaded"

            def render(self):
                match self.value.status:
                    case Pending():
                        return Text("parent:Pending")
                    case Failed(error=error):
                        return Text(f"parent:Failed:{error}")
                    case Ready():
                        return [Text("parent:Ready"), self.boundary(self.child, key="child")]

        message: Any = fake_message()
        message_root = MessageRoot(Parent(), access=Everyone(), timeout=None)

        await message_root.send(_Destination(message))

        assert loads == ["parent", "child"]
        assert message.edit.await_count == 2
        assert "child:Ready" in str(message.edit.await_args.kwargs["view"].to_components())

    async def test_hidden_resource_waits_until_its_branch_is_rendered(self) -> None:

        loads: list[str] = []

        class Conditional(Component):
            shown: bool = state(default=False)

            @resource
            async def value(self) -> str:
                loads.append("load")
                return "loaded"

            def render(self):
                return Text(type(self.value.status).__name__) if self.shown else Text("hidden")

        panel = Conditional()
        message: Any = fake_message()
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        await message_root.send(_Destination(message))

        assert loads == []
        panel.shown = True
        await message_root.refresh()

        assert loads == ["load"]
        assert message.edit.await_count == 2
        assert "Ready" in str(message.edit.await_args.kwargs["view"].to_components())

    async def test_a_destination_without_an_edit_handle_leaves_loading_pending(self) -> None:
        loads: list[str] = []

        async def load(_key: str) -> str:
            loads.append("load")
            return "loaded"

        panel = VisibleResourcePanel(load)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)

        await message_root.send(_Destination(None))

        assert loads == []
        assert isinstance(panel.value.status, Pending)
        assert message_root.pending

    async def test_dependency_reload_uses_the_interaction_for_both_paints(self) -> None:
        async def load(key: str) -> str:
            return key

        panel = VisibleResourcePanel(load)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        await message_root.send(_Destination(fake_message()))
        interaction = fake_interaction()

        await message_root.dispatch("change", interaction)

        interaction.response.edit_message.assert_awaited_once()
        assert "pending:first" in str(interaction.response.edit_message.await_args.kwargs["view"].to_components())
        interaction.followup.edit_message.assert_awaited_once()
        assert "ready:second" in str(interaction.followup.edit_message.await_args.kwargs["view"].to_components())

    async def test_a_failed_settled_edit_keeps_the_pending_generation_repairable(self) -> None:
        async def load(_key: str) -> str:
            return "loaded"

        panel = VisibleResourcePanel(load)
        message: Any = fake_message()
        message.edit.side_effect = _http_error()
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)

        await message_root.send(_Destination(message))

        assert isinstance(panel.value.status, Ready)
        assert message_root.pending
        assert message_root._view is not None
        assert "pending" in str(message_root._view.to_components())

        message.edit.side_effect = None
        message.edit.return_value = message
        await message_root.refresh()

        assert not message_root.pending
        assert message_root._view is not None
        assert "ready:loaded" in str(message_root._view.to_components())

    async def test_a_load_superseded_mid_settle_is_abandoned(self) -> None:
        """The mount supplies the cancellation `abandon_superseded_loads` asks for."""
        panel = CheckpointedResourcePanel()
        message: Any = fake_message()
        destination = _Destination(message)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)

        # Deadlined because the regression is a hang, not a wrong answer: without the mount
        # installing a scope, nothing releases the first loader and `send` never returns.
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(message_root.send, destination)
                await panel.entered.wait()
                panel.value.invalidate()

        assert "ready:attempt-2" in str(destination.calls[0][0].to_components())
        assert panel.finished == [2], "the superseded loader stopped instead of running on"
        assert not panel.released.is_set(), "nothing had to release it, which is the point"


class Leaf(Component):
    """A component that only knows what it renders once its `on_load` has run."""

    label: str = state("")

    def __init__(self, log: list[str], name: str = "leaf") -> None:
        self.log = log
        self.name = name

    async def on_load(self) -> None:
        self.log.append(f"load:{self.name}")
        self.label = f"{self.name} loaded"

    def render(self):
        self.log.append(f"render:{self.name}")
        return Text(self.label)


class Host(Component):
    """A loading parent whose child is only reachable through its loaded render."""

    ready: bool = state(default=False)

    def __init__(self, log: list[str], child: Component) -> None:
        self.log = log
        self.child = child

    async def on_load(self) -> None:
        self.log.append("load:host")
        self.ready = True

    def render(self):
        self.log.append("render:host")
        nodes: list[LayoutNode] = [Text("host")]
        if self.ready:
            nodes.append(self.boundary(self.child, key="child"))
        return nodes


class TestDeferredExpansion:
    """A discovery render: what `on_load` needs to run before anything renders."""

    def test_expansion_stops_at_a_deferred_child(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)

        tree = runtime.render(defer=lambda component: component is child)

        assert tree.deferred == (child,)
        assert "render:child" not in log
        # Set before the defer check, so a deferred child's on_load still invalidates.
        assert child._parent is host

    def test_a_deferred_child_still_invalidates_through_its_parent(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        invalidated: list[bool] = []
        runtime = ComponentRuntime(host, on_invalidate=lambda: invalidated.append(True))
        runtime.render(defer=lambda component: component is child)

        child.label = "written by a load"

        assert invalidated

    def test_a_discovery_tree_cannot_be_committed(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)
        tree = runtime.render(defer=lambda component: component is child)

        with pytest.raises(LayoutInvariantError, match="discovery render"):
            runtime.commit(tree)

    def test_no_defer_predicate_expands_everything(self):
        log: list[str] = []
        child = Leaf(log, "child")
        host = Host(log, child)
        host.ready = True
        runtime = ComponentRuntime(host)

        tree = runtime.render()

        assert tree.deferred == ()
        assert "render:child" in log


class Nested(Component):
    """A loading parent that embeds a loading child, for the tiered-pass case."""

    ready: bool = state(default=False)

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.child = Leaf(log, "child")

    async def on_load(self) -> None:
        self.log.append("load:parent")
        self.ready = True

    def render(self):
        self.log.append("render:parent")
        nodes: list[LayoutNode] = [Text("parent")]
        if self.ready:
            nodes.append(self.boundary(self.child, key="child"))
        return nodes


class Siblings(Component):
    """Two children that enter the tree together, so their loads share one task group."""

    def __init__(self, log: list[str], *, first: Component, second: Component) -> None:
        self.log = log
        self.first = first
        self.second = second

    def render(self):
        return [self.boundary(self.first, key="first"), self.boundary(self.second, key="second")]


class TestLoading:
    """`on_load` runs before the first render that would show the component."""

    async def test_the_delivered_render_is_the_loaded_one(self):
        log: list[str] = []
        message_root = MessageRoot(Leaf(log, "panel"), access=Everyone(), timeout=None)
        destination = _Destination(fake_message())

        await message_root.send(destination)

        assert log == ["load:panel", "render:panel"]
        assert len(destination.calls) == 1
        view, _files = destination.calls[0]
        assert "panel loaded" in str(view.to_components())
        assert not message_root.pending

    async def test_a_child_loads_before_its_own_first_render(self):
        log: list[str] = []
        message_root = MessageRoot(Nested(log), access=Everyone(), timeout=None)

        await message_root.send(delivered_to(fake_message()))

        # The parent's loaded render is what reveals the child, so the tiers are serial —
        # but no component renders before its own load.
        assert log.index("load:child") < log.index("render:child")
        assert log.index("load:parent") < log.index("render:parent")

    async def test_siblings_load_together_and_coalesce_into_one_paint(self):
        log: list[str] = []
        started = anyio.Event()

        class Slow(Leaf):
            async def on_load(self) -> None:
                started.set()
                await super().on_load()

        class Waits(Leaf):
            async def on_load(self) -> None:
                # Deadlocks unless the sibling is genuinely in flight at the same time.
                await started.wait()
                await super().on_load()

        component = Siblings(log, first=Waits(log, "waits"), second=Slow(log, "slow"))
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        destination = _Destination(fake_message())

        with anyio.fail_after(5):
            await message_root.send(destination)

        assert len(destination.calls) == 1
        rendered = str(destination.calls[0][0].to_components())
        assert "waits loaded" in rendered
        assert "slow loaded" in rendered

    async def test_a_component_embedded_mid_session_loads_before_the_edit(self):
        log: list[str] = []

        class OpenContext(Component):
            open: bool = state(default=False)

            def __init__(self) -> None:
                self.child = Leaf(log, "child")

            def render(self):
                nodes: list[LayoutNode] = [Row((Button("open", self.reveal, "open"),))]
                if self.open:
                    nodes.append(self.boundary(self.child, key="child"))
                return nodes

            async def reveal(self, event: PressEvent) -> None:
                self.open = True

        message_root = MessageRoot(OpenContext(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(fake_message()))
        assert log == []

        interaction = fake_interaction()
        await message_root.dispatch("open", interaction)

        assert log.index("load:child") < log.index("render:child")
        assert "child loaded" in str(interaction.response.edit_message.await_args.kwargs["view"].to_components())

    async def test_a_failed_load_delivers_nothing_and_stays_retryable(self):
        attempts: list[int] = []

        class Flaky(Component):
            label: str = state("")

            async def on_load(self) -> None:
                attempts.append(1)
                if len(attempts) == 1:
                    message = "the database is down"
                    raise RuntimeError(message)
                self.label = "loaded"

            def render(self):
                return Text(self.label)

        message_root = MessageRoot(Flaky(), access=Everyone(), timeout=None)
        destination = _Destination(fake_message())

        with pytest.raises(RuntimeError, match="the database is down"):
            await message_root.send(destination)

        assert destination.calls == []
        assert message_root._generation == 0

        await message_root.send(destination)

        assert len(attempts) == 2
        assert "loaded" in str(destination.calls[0][0].to_components())

    async def test_a_lone_failure_arrives_unwrapped(self):
        """Error routing downstream of a mount is isinstance-based, not `except*`-based."""

        class Boom(Leaf):
            async def on_load(self) -> None:
                message = "no such account"
                raise LookupError(message)

        log: list[str] = []
        component = Siblings(log, first=Boom(log, "boom"), second=Leaf(log, "fine"))
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        with pytest.raises(LookupError, match="no such account"):
            await message_root.send(_Destination(fake_message()))

    async def test_several_failures_at_once_stay_a_group(self):
        class Boom(Leaf):
            async def on_load(self) -> None:
                await anyio.sleep(0)
                message = f"{self.name} failed"
                raise RuntimeError(message)

        log: list[str] = []
        component = Siblings(log, first=Boom(log, "first"), second=Boom(log, "second"))
        message_root = MessageRoot(component, access=Everyone(), timeout=None)

        with pytest.raises(BaseExceptionGroup) as caught:
            await message_root.send(_Destination(fake_message()))

        assert len(caught.value.exceptions) == 2

    async def test_a_completed_load_does_not_run_again(self):
        log: list[str] = []
        component = Leaf(log, "panel")
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        destination = _Destination(fake_message(), raises=_http_error())

        with pytest.raises(discord.HTTPException):
            await message_root.send(destination)
        destination.raises = None
        await message_root.send(destination)
        component.label = "changed"
        await message_root.refresh()

        assert log.count("load:panel") == 1

    async def test_stage_view_renders_without_loading(self):
        """The stage-only escape hatch is sync, so it cannot load — and does not pretend to."""
        log: list[str] = []
        message_root = MessageRoot(Leaf(log, "panel"), access=Everyone(), timeout=None)

        message_root._stage_view()
        await message_root.finish(disable=True)

        assert log == ["render:panel"]

    async def test_a_terminal_render_loads_nothing(self):
        log: list[str] = []
        component = Nested(log)
        message_root = MessageRoot(component, access=Everyone(), timeout=None)
        await message_root.send(delivered_to(fake_message()))
        component.child = Leaf(log, "late")
        log.clear()

        await message_root.finish(disable=True)

        assert not any(entry.startswith("load:") for entry in log)

    async def test_a_tree_declaring_no_loads_takes_no_extra_render(self):
        renders: list[int] = []

        class Plain(Component):
            count: int = state(0)

            def render(self):
                renders.append(self.count)
                return Text(f"count: {self.count}")

        message_root = MessageRoot(Plain(), access=Everyone(), timeout=None)

        await message_root.send(delivered_to(fake_message()))

        assert len(renders) == 1

    async def test_a_load_writes_plain_state_outside_any_transaction(self):
        """Plan 08's tracking covers handlers; a load is ordinary pre-delivery state."""
        seen: list[bool] = []

        class Reader(Component):
            label: str = state("")

            async def on_load(self) -> None:
                seen.append(_CURRENT.get() is not None)
                self.untracked = "a plain attribute, written with nothing watching"
                self.label = "loaded"

            def render(self):
                return Text(self.label)

        message_root = MessageRoot(Reader(), access=Everyone(), timeout=None)
        await message_root.send(delivered_to(fake_message()))

        assert seen == [False]

    async def test_a_load_that_never_settles_is_reported(self):
        class Endless(Component):
            depth: int = state(0)

            def __init__(self) -> None:
                self.child: Endless | None = None

            async def on_load(self) -> None:
                self.child = Endless()
                self.child.depth = self.depth + 1

            def render(self):
                nodes: list[LayoutNode] = [Text(f"depth {self.depth}")]
                if self.child is not None:
                    nodes.append(self.boundary(self.child, key="child"))
                return nodes

        message_root = MessageRoot(Endless(), access=Everyone(), timeout=None)

        with pytest.raises(LayoutInvariantError, match="did not settle"):
            await message_root.send(delivered_to(fake_message()))


class _GuardedPanel(Component):
    """One semantic action whose admission and busy policy the test supplies."""

    count: int = state(0)

    def __init__(
        self,
        *,
        guard: sl.guards.Guard | None = None,
        busy: sl.interactions.BusySpec | None = None,
        mode: ActionMode = ActionMode.EXCLUSIVE,
        run: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self.guard = guard
        self.busy = busy
        self.mode = mode
        self.run = run

    def render(self):
        return sl.action_controls(
            sl.action_control(
                "Go",
                self.go,
                key="go",
                guard=self.guard,
                busy=self.busy,
                mode=self.mode,
            ),
            key="panel",
        )

    async def go(self, event: ActionEvent) -> None:
        if self.run is not None:
            await self.run()
        self.count += 1


def _notice_text(interaction: Any) -> list[str]:
    view = interaction.response.send_message.await_args.kwargs["view"]
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class TestGuards:
    async def test_a_denial_runs_no_handler_and_costs_one_ephemeral_message(self):
        panel = _GuardedPanel(guard=sl.guards.when(lambda event: False, reason="Not for you."))
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        commit_render(message_root)
        generation = message_root.generation
        interaction = fake_interaction()

        await message_root.dispatch("go", interaction)

        assert panel.count == 0
        assert message_root.generation == generation
        assert _notice_text(interaction) == ["Not for you."]
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    async def test_a_reasonless_denial_falls_back_to_chrome(self):
        message_root = MessageRoot(_GuardedPanel(guard=sl.guards.once()), access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("go", fake_interaction())

        interaction = fake_interaction()
        await message_root.dispatch("go", interaction)

        assert _notice_text(interaction) == [Chrome().not_now]

    async def test_a_delay_bearing_denial_says_how_long_to_wait(self):
        message_root = MessageRoot(_GuardedPanel(guard=sl.guards.cooldown(30)), access=Everyone(), timeout=None)
        commit_render(message_root)
        await message_root.dispatch("go", fake_interaction())

        interaction = fake_interaction()
        await message_root.dispatch("go", interaction)

        assert _notice_text(interaction) == ["Try again in 30 seconds."]

    async def test_a_denial_is_traced_as_its_own_disposition(self):
        profiler = MemoryProfiler()
        message_root = MessageRoot(
            _GuardedPanel(guard=sl.guards.when(lambda event: False, reason="No.")),
            access=Everyone(),
            timeout=None,
            profiler=profiler,
        )
        commit_render(message_root)

        await message_root.dispatch("go", fake_interaction())

        dispatch = _profile_trace(profiler).result.dispatch
        assert dispatch is not None
        assert dispatch.disposition is DispatchDisposition.GUARD_DENIED
        assert dispatch.action is ActionStatus.NOT_RUN

    async def test_a_raising_guard_reaches_the_error_hook_without_admitting(self):
        error = RuntimeError("permission service unavailable")

        async def broken(event) -> bool:
            raise error

        hook = AsyncMock()
        profiler = MemoryProfiler()
        panel = _GuardedPanel(guard=sl.guards.permission(broken))
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, on_error=hook, profiler=profiler)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("go", interaction)

        assert panel.count == 0
        hook.assert_awaited_once_with(interaction, error, "guard:go")
        dispatch = _profile_trace(profiler).result.dispatch
        assert dispatch is not None
        assert dispatch.disposition is DispatchDisposition.GUARD_FAILED

    async def test_admission_runs_under_every_concurrency_policy(self):
        # A list rather than component state: PARALLEL_READ handlers may not write, and the
        # question here is whether the guard was consulted at all under each policy.
        class Reader(Component):
            def __init__(self, mode: ActionMode) -> None:
                self.mode = mode
                self.presses: list[str] = []

            def render(self):
                return sl.action_controls(
                    sl.action_control("Go", self.go, key="go", guard=sl.guards.once(), mode=self.mode),
                    key="panel",
                )

            async def go(self, event: ActionEvent) -> None:
                self.presses.append(event.actor.id)

        for policy in ActionMode:
            panel = Reader(policy)
            message_root = MessageRoot(panel, access=Everyone(), timeout=None)
            commit_render(message_root)

            await message_root.dispatch("go", fake_interaction())
            await message_root.dispatch("go", fake_interaction())

            assert panel.presses == ["1"], policy

    async def test_a_stale_press_is_rejected_before_its_guard_is_consulted(self):
        panel = _GuardedPanel(guard=sl.guards.once())
        message_root = MessageRoot(panel, access=Everyone(), timeout=None)
        commit_render(message_root)
        stale = message_root.generation - 1

        await message_root.dispatch("go", fake_interaction(), generation=stale)
        await message_root.dispatch("go", fake_interaction())

        assert panel.count == 1

    async def test_a_guard_survives_the_collapse_of_a_row_into_a_select(self):
        class Crowd(Component):
            pressed: int = state(0)

            def render(self):
                return sl.action_controls(
                    *(
                        sl.action_control(f"Act {index}", self.act, key=f"act{index}", guard=sl.guards.once())
                        for index in range(8)
                    ),
                    key="crowd",
                    display=sl.semantic.ControlDisplay.GROUPED,
                )

            async def act(self, event: ActionEvent) -> None:
                self.pressed += 1

        crowd = Crowd()
        message_root = MessageRoot(crowd, access=Everyone(), timeout=None)
        commit_render(message_root)
        (picker,) = set(message_root.snapshot().handler_keys) - {f"act{index}" for index in range(8)}

        await message_root.dispatch(picker, fake_interaction(), ["act3"])
        await message_root.dispatch(picker, fake_interaction(), ["act3"])
        await message_root.dispatch(picker, fake_interaction(), ["act4"])

        assert crowd.pressed == 2

    async def test_a_form_trigger_guards_the_press_and_not_the_submission(self):
        submitted = AsyncMock()
        spec = FormSpec("Rename", (TextField(key="name", label="Name"),))

        class Panel(Component):
            seen: int = state(0)

            def render(self):
                return sl_form("Rename", spec, key="rename", on_submit=submitted, guard=sl.guards.once())

        message_root = MessageRoot(Panel(), access=Everyone(), timeout=None)
        commit_render(message_root)

        opened = fake_interaction()
        await message_root.dispatch("rename", opened)
        assert opened.response.send_modal.await_count == 1

        refused = fake_interaction()
        await message_root.dispatch("rename", refused)
        assert refused.response.send_modal.await_count == 0

        # The submission completes a press already admitted, so `once` does not eat it.
        await message_root.dispatch_submit("rename", fake_interaction(), spec, {"name": "Ada"}, submitted)
        submitted.assert_awaited_once()


class TestBusyFeedback:
    @staticmethod
    def _labels(view: discord.ui.LayoutView) -> list[tuple[object, bool]]:
        return [(item.label, item.disabled) for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    @staticmethod
    async def _press(message_root: MessageRoot, interaction: Any, release: asyncio.Event) -> None:
        """Dispatch a press whose handler is held open until the interim has painted."""

        async def press() -> None:
            await message_root.dispatch("go", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(press)
            while not interaction.response.edit_message.await_count:
                await asyncio.sleep(0)
            release.set()

    async def test_a_fast_handler_suppresses_an_unchanged_finished_render(self):
        panel = _GuardedPanel(busy=sl.interactions.BusySpec())
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=30)
        commit_render(message_root)
        interaction = fake_interaction()

        await message_root.dispatch("go", interaction)

        assert panel.count == 1
        interaction.response.edit_message.assert_not_awaited()
        interaction.response.defer.assert_awaited_once()
        assert message_root.snapshot().suppressed == 1

    async def test_a_slow_handler_disables_the_panel_and_relabels_the_press(self):
        release = asyncio.Event()
        panel = _GuardedPanel(busy=sl.interactions.BusySpec(pending="Rendering…"), run=release.wait)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0)
        commit_render(message_root)
        interaction = fake_interaction()

        await self._press(message_root, interaction, release)

        interim = interaction.response.edit_message.await_args.kwargs["view"]
        assert self._labels(interim) == [("Rendering…", True)]
        # The interim edit answered the click, so nothing deferred it.
        assert interaction.response.defer.await_count == 0
        assert panel.count == 1
        final = interaction.followup.edit_message.await_args.kwargs["view"]
        assert self._labels(final) == [("Go", False)]

    async def test_a_blocked_busy_paint_cannot_delay_acknowledgement(self) -> None:
        release_lock = asyncio.Event()
        release_handler = asyncio.Event()

        class Idle(Component):
            def render(self):
                return sl.action_controls(
                    sl.action_control("Go", self.go, key="go", busy=sl.interactions.BusySpec(pending="Working…")),
                    key="panel",
                )

            async def go(self, event: ActionEvent) -> None:
                await release_handler.wait()

        message_root = MessageRoot(
            Idle(),
            access=Everyone(),
            timeout=None,
            pending_after=0,
            acknowledgement_timeout=0.01,
        )
        commit_render(message_root)
        interaction = fake_interaction()
        lock_held = asyncio.Event()

        async def hold_render_lock() -> None:
            async with message_root._render_lock:
                lock_held.set()
                await release_lock.wait()

        async def dispatch() -> None:
            await message_root.dispatch("go", interaction)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(hold_render_lock)
            await lock_held.wait()
            tasks.start_soon(dispatch)
            with anyio.fail_after(0.2):
                while not interaction.response.defer.await_count:
                    await asyncio.sleep(0)

            assert interaction.response.edit_message.await_count == 0
            assert interaction.followup.edit_message.await_count == 0
            release_lock.set()
            with anyio.fail_after(0.2):
                while not interaction.followup.edit_message.await_count:
                    await asyncio.sleep(0)

            interim = interaction.followup.edit_message.await_args_list[0].kwargs["view"]
            assert self._labels(interim) == [("Working…", True)]
            release_handler.set()

        assert interaction.response.defer.await_count == 1
        assert interaction.followup.edit_message.await_count == 2
        restored = interaction.followup.edit_message.await_args_list[1].kwargs["view"]
        assert self._labels(restored) == [("Go", False)]

    def test_acknowledgement_timeout_must_precede_discords_deadline(self) -> None:
        with pytest.raises(
            ValueError,
            match="a mount acknowledgement timeout must be greater than zero and below Discord's 3-second limit",
        ):
            MessageRoot(Counter(), access=Everyone(), acknowledgement_timeout=3.5)

    async def test_the_pending_label_falls_back_to_chrome(self):
        release = asyncio.Event()
        panel = _GuardedPanel(busy=sl.interactions.BusySpec(), run=release.wait)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0)
        commit_render(message_root)
        interaction = fake_interaction()

        await self._press(message_root, interaction, release)

        interim = interaction.response.edit_message.await_args.kwargs["view"]
        assert self._labels(interim) == [(Chrome().working, True)]

    async def test_a_handler_error_puts_the_live_panel_back_before_the_error_hook(self):
        order: list[str] = []
        release = asyncio.Event()

        async def fail() -> None:
            await release.wait()
            raise RuntimeError("render failed")

        async def hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
            order.append(f"hook:{source}")

        panel = _GuardedPanel(busy=sl.interactions.BusySpec(), run=fail)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0, on_error=hook)
        commit_render(message_root)
        interaction = fake_interaction()

        await self._press(message_root, interaction, release)

        restored = interaction.followup.edit_message.await_args.kwargs["view"]
        assert self._labels(restored) == [("Go", False)]
        assert order == ["hook:action:go"]
        assert panel.count == 0

    async def test_restore_on_error_false_leaves_the_interim_up(self):
        release = asyncio.Event()

        async def fail() -> None:
            await release.wait()
            raise RuntimeError("render failed")

        panel = _GuardedPanel(busy=sl.interactions.BusySpec(restore_on_error=False), run=fail)
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0, on_error=AsyncMock())
        commit_render(message_root)
        interaction = fake_interaction()

        await self._press(message_root, interaction, release)

        assert interaction.followup.edit_message.await_count == 0

    async def test_an_action_that_changes_nothing_still_restores_the_panel(self):
        release = asyncio.Event()

        ran: list[bool] = []

        class Idle(Component):
            def render(self):
                return sl.action_controls(
                    sl.action_control("Go", self.go, key="go", busy=sl.interactions.BusySpec()), key="panel"
                )

            async def go(self, event: ActionEvent) -> None:
                await release.wait()
                ran.append(True)

        panel = Idle()
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0)
        commit_render(message_root)
        interaction = fake_interaction()

        await self._press(message_root, interaction, release)

        assert ran == [True]
        assert not message_root.pending
        restored = interaction.followup.edit_message.await_args.kwargs["view"]
        assert self._labels(restored) == [("Go", False)]

    async def test_a_late_watchdog_paints_nothing_over_the_finished_render(self):
        panel = _GuardedPanel(busy=sl.interactions.BusySpec())
        message_root = MessageRoot(panel, access=Everyone(), timeout=None, pending_after=0)
        commit_render(message_root)
        interaction = fake_interaction()
        busy = _BusyPaint(message_root, "go", sl.interactions.BusySpec(), interaction)
        profile = SimpleNamespace(acknowledge=lambda source: None)

        assert await busy.close() is False
        await busy.show(cast(Any, profile))

        assert busy.shown is False
        assert interaction.response.edit_message.await_count == 0
