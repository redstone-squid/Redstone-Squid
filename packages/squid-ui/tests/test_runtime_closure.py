"""Closing a runtime ends rendering without preventing cleanup or inspection."""

import gc
import weakref
from typing import override

import pytest

import squid_ui as ui
from squid_ui.context import ContextKey
from squid_ui.ext.discord import DISCORD_V2_DPY27
from squid_ui.runtime import ComponentRuntime


class Panel(ui.Component):
    @override
    def render(self) -> ui.LayoutNode:
        return ui.paragraph("panel")


@pytest.mark.parametrize("terminal", [False, True])
def test_closed_runtime_rejects_new_work_without_changing_retained_state(terminal: bool) -> None:
    key = ContextKey[str]("label")
    panel = Panel()
    runtime = ComponentRuntime(panel, context={key: "before"})
    tree = runtime.render()
    candidate = runtime.stage(tree)
    assert not runtime.closed
    runtime.finish()
    assert runtime.closed
    assert candidate.settled

    with pytest.raises(RuntimeError, match="runtime is closed"):
        runtime.render(terminal=terminal)
    with pytest.raises(RuntimeError, match="runtime is closed"):
        runtime.stage(tree)
    with pytest.raises(RuntimeError, match="runtime is closed"):
        runtime.commit(tree)
    with pytest.raises(RuntimeError, match="runtime is closed"):
        runtime.set_context(key, "after")
    with pytest.raises(RuntimeError, match="runtime is closed"):
        runtime.claim(panel)
    with pytest.raises(ui.LayoutInvariantError, match="already settled"):
        candidate.commit()
    with pytest.raises(AttributeError):
        runtime.closed = False  # type: ignore  # Verify runtime rejection as well as the read-only annotation.
    assert runtime.context[key] == "before"
    assert runtime.components == {}
    assert runtime.pending_cached_atomic_resources() == ()


@pytest.mark.parametrize("fail", [False, True])
def test_finish_is_final_and_reentrant_even_when_an_unmount_hook_fails(fail: bool) -> None:
    events: list[str] = []

    class ClosingPanel(Panel):
        @override
        def on_unmount(self) -> None:
            assert runtime.closed
            events.append("unmount")
            runtime.finish()
            runtime.invalidate(self)
            if fail:
                raise ValueError("unmount failed")

    panel = ClosingPanel()
    runtime = ComponentRuntime(panel, on_invalidate=lambda: events.append("invalidated"))
    runtime.commit(runtime.render())
    if fail:
        with pytest.raises(ExceptionGroup, match="lifecycle hooks"):
            runtime.finish()
    else:
        runtime.finish()
    runtime.finish()
    runtime.invalidate()
    runtime.discard()
    assert events == ["unmount"]
    assert runtime.closed
    assert runtime.components == {}
    assert runtime.subscriptions.subscribed_addresses == ()

    replacement = ComponentRuntime(panel)
    replacement.commit(replacement.render())
    runtime.finish()
    assert not replacement.closed
    assert replacement.components == {"$": panel}
    # Avoid repeating the deliberately failing hook in the replacement runtime.
    if fail:
        with pytest.raises(ExceptionGroup, match="lifecycle hooks"):
            replacement.finish()
    else:
        replacement.finish()


async def test_service_waits_remain_available_after_closure() -> None:
    runtime = ComponentRuntime(Panel())
    runtime.commit(runtime.render())
    runtime.finish()
    runtime.check_service_wait()
    await runtime.wait_services_ready()
    await runtime.wait_services()
    await runtime.wait_services(allow_reentry=True)
    assert runtime.closed


def test_finish_releases_live_planning_generations() -> None:
    class Receiver:
        async def handle(self, event: ui.PressEvent) -> None:
            pass

    runtime = ComponentRuntime(Panel())
    receiver = Receiver()
    reference = weakref.ref(receiver)
    document = ui.Document((ui.action_controls(ui.action_control("Run", receiver.handle, key="run"), key="actions"),))
    result = ui.planning.plan(
        document, target=DISCORD_V2_DPY27, session=runtime.presentation, snapshot=runtime.planning_snapshot
    )
    runtime.presentation.apply_updates(result.session_updates)
    runtime.planning_snapshot.commit(result, runtime.presentation)
    del result, document, receiver
    gc.collect()
    assert reference() is not None
    runtime.finish()
    gc.collect()
    assert reference() is None
    assert runtime.planning_snapshot.committed is None
    assert runtime.presentation.retained_composition is None
    assert runtime.planning_snapshot.fingerprints.snapshot().entries == 0
