"""Reveal commands belong to action commit and successful delivery."""

from typing import override

import anyio
import pytest

import squid_ui as sl
from squid_reactivity.transactions import readonly_transaction
from squid_ui.ext.discord import Everyone, MessageRoot
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.testing import stage
from squid_ui.interactions import Actor, PressEvent
from squid_ui.runtime import ComponentRuntime, transaction
from squid_ui.testing import RecordingResponder


class Rows(sl.Component):
    async def _noop(self, event: sl.PressEvent) -> None:
        await event.acknowledge()

    async def _reveal(self, event: sl.PressEvent) -> None:
        event.reveal(sl.element_ref(sl.semantic.ActionControl, "row-2"))

    @override
    def render(self) -> sl.LayoutNode:
        return sl.stack(
            sl.action_controls(sl.action_control("Reveal last", self._reveal, key="reveal"), key="persistent"),
            sl.region(
                *(
                    sl.action_controls(
                        sl.action_control(str(i), self._noop, key=f"row-{i}"), key=f"actions-{i}"
                    ).unbreakable()
                    for i in range(3)
                ),
                key="rows",
                title="",
                page_size=1,
            ),
        )


async def test_same_target_can_be_revealed_again_without_sticking_to_later_renders() -> None:
    async with stage(Rows(), owner=object()) as ui:
        ui.control(ui.key_for("row-0"))
        await ui.press(ui.key_for("reveal"))
        ui.control(ui.key_for("row-2"))
        assert not ui.root.presentation.pending_reveals
        await ui.press(
            next(
                key for key in ui.root.snapshot().handler_keys if "__cursor_previous." in key and key.endswith("/rows")
            )
        )
        await ui.press(
            next(
                key for key in ui.root.snapshot().handler_keys if "__cursor_previous." in key and key.endswith("/rows")
            )
        )
        ui.control(ui.key_for("row-0"))
        await ui.root.refresh()
        ui.control(ui.key_for("row-0"))
        await ui.press(ui.key_for("reveal"))
        ui.control(ui.key_for("row-2"))


async def test_failed_delivery_preserves_the_request_and_old_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    async with stage(Rows(), owner=object()) as ui:
        old = ui.root.presentation.cursor("$/rows")
        with monkeypatch.context() as patch:

            async def fail(*args: object, **kwargs: object) -> None:
                raise RuntimeError("delivery failed")

            patch.setattr(ui.root, "_deliver", fail)
            with pytest.raises(RuntimeError, match="delivery failed"):
                await ui.press(ui.key_for("reveal"))
        assert ui.root.presentation.cursor("$/rows") == old
        assert ui.root.presentation.pending_reveals
        ui.control(ui.key_for("row-0"))
        await ui.root.refresh()
        ui.control(ui.key_for("row-2"))
        assert not ui.root.presentation.pending_reveals


@pytest.mark.parametrize("cancel", [False, True])
async def test_rollback_discards_reveal_commands(cancel: bool) -> None:
    runtime = ComponentRuntime(Rows())
    runtime.commit(runtime.render())
    scope = runtime.presentation_cells.for_key("reveal")
    assert scope is not None
    error = anyio.get_cancelled_exc_class() if cancel else ValueError
    with pytest.raises(error), scope.activate(), transaction() as tx:
        event = PressEvent(Actor("reader"), RecordingResponder(), context=object(), _transaction=tx)
        event.reveal(sl.element_ref(sl.semantic.ActionControl, "row-2"))
        if cancel:
            raise anyio.get_cancelled_exc_class()()
        raise ValueError("action failed")
    assert not runtime.presentation.pending_reveals
    runtime.finish()


async def test_shared_instance_reveal_is_scoped_to_the_pressed_placement() -> None:
    class Screen(sl.Component):
        def __init__(self) -> None:
            self.rows = Rows()

        @override
        def render(self) -> sl.LayoutNode:
            return sl.stack(self.rows.keyed("left"), self.rows.keyed("right"))

    async with stage(Screen(), owner=object()) as ui:
        await ui.press(ui.key_for("left.reveal"))
        ui.control(ui.key_for("left.row-2"))
        ui.control(ui.key_for("right.row-0"))
        assert not ui.root.presentation.pending_reveals


async def test_older_candidate_does_not_consume_a_newer_request_for_the_same_target() -> None:
    root = MessageRoot(Rows(), access=Everyone(), timeout=None)
    message = sd.message_harness()
    await root.send(sd.delivered_to(message))
    scope = root.runtime.presentation_cells.for_key("reveal")
    assert scope is not None
    reference = sl.element_ref(sl.semantic.ActionControl, "row-2")
    root.presentation.request_reveal(scope, reference)
    candidate = root._stage()
    root.presentation.request_reveal(scope, reference)
    latest = tuple(root.presentation.pending_reveals.values())
    root.presentation.apply_updates(candidate.session_updates)
    assert tuple(root.presentation.pending_reveals.values()) == latest
    root._rollback(candidate)
    await root.finish()


async def test_latest_commands_win_per_region_and_independent_regions_move_together() -> None:
    class Pair(Rows):
        async def _reveal(self, event: sl.PressEvent) -> None:
            event.reveal(sl.element_ref(sl.semantic.ActionControl, "left-2"))
            event.reveal(sl.element_ref(sl.semantic.ActionControl, "right-1"))
            event.reveal(sl.element_ref(sl.semantic.ActionControl, "left-0"))

        @override
        def render(self) -> sl.LayoutNode:
            return sl.stack(
                sl.action_controls(sl.action_control("Reveal", self._reveal, key="reveal"), key="persistent"),
                *(
                    sl.region(
                        *(
                            sl.action_controls(
                                sl.action_control(str(i), self._noop, key=f"{name}-{i}"), key=f"{name}-actions-{i}"
                            ).unbreakable()
                            for i in range(3)
                        ),
                        key=name,
                        title="",
                        page_size=1,
                    )
                    for name in ("left", "right")
                ),
            )

    async with stage(Pair(), owner=object()) as ui:
        await ui.press(ui.key_for("reveal"))
        ui.control(ui.key_for("left-0"))
        ui.control(ui.key_for("right-1"))
        assert not ui.root.presentation.pending_reveals
        await ui.root.refresh()
        ui.control(ui.key_for("left-0"))
        ui.control(ui.key_for("right-1"))


async def test_reveal_selects_both_nested_region_coordinates() -> None:
    class Nested(Rows):
        @override
        def render(self) -> sl.LayoutNode:
            return sl.stack(
                sl.action_controls(sl.action_control("Reveal", self._reveal, key="reveal"), key="persistent"),
                sl.region(
                    sl.paragraph("Introduction").unbreakable(),
                    sl.region(
                        *(
                            sl.action_controls(
                                sl.action_control(str(i), self._noop, key=f"row-{i}"), key=f"actions-{i}"
                            ).unbreakable()
                            for i in range(3)
                        ),
                        key="inner",
                        title="",
                        page_size=1,
                    ),
                    key="outer",
                    title="",
                    page_size=1,
                ),
            )

    async with stage(Nested(), owner=object()) as ui:
        assert "Introduction" in ui.texts()
        await ui.press(ui.key_for("reveal"))
        ui.control(ui.key_for("row-2"))
        assert "Introduction" not in ui.texts()
        assert not ui.root.presentation.pending_reveals


async def test_later_page_turn_supersedes_a_failed_reveal(monkeypatch: pytest.MonkeyPatch) -> None:
    async with stage(Rows(), owner=object()) as ui:
        with monkeypatch.context() as patch:

            async def fail(*args: object, **kwargs: object) -> None:
                raise RuntimeError("delivery failed")

            patch.setattr(ui.root, "_deliver", fail)
            with pytest.raises(RuntimeError, match="delivery failed"):
                await ui.press(ui.key_for("reveal"))
        next_page = next(
            key for key in ui.root.snapshot().handler_keys if "__cursor_next." in key and key.endswith("/rows")
        )
        await ui.press(next_page)
        ui.control(ui.key_for("row-1"))
        assert not ui.root.presentation.pending_reveals
        await ui.root.refresh()
        ui.control(ui.key_for("row-1"))


async def test_absent_target_is_a_one_shot_noop() -> None:
    class Missing(Rows):
        async def _reveal(self, event: sl.PressEvent) -> None:
            event.reveal(sl.element_ref(sl.semantic.ActionControl, "absent"))

    async with stage(Missing(), owner=object()) as ui:
        await ui.press(ui.key_for("reveal"))
        ui.control(ui.key_for("row-0"))
        assert not ui.root.presentation.pending_reveals


async def test_reveal_can_certify_a_partial_destination() -> None:
    class Partial(Rows):
        async def _reveal(self, event: sl.PressEvent) -> None:
            event.reveal(sl.element_ref(sl.semantic.ListItem, "last"))

        @override
        def render(self) -> sl.LayoutNode:
            return sl.stack(
                sl.action_controls(sl.action_control("Reveal", self._reveal, key="reveal"), key="persistent"),
                sl.bullets(
                    sl.bullet("a" * 2100, key="first"),
                    sl.bullet("b" * 5000, key="middle"),
                    sl.bullet("c" * 2100, key="last"),
                    key="items",
                    group_size=1,
                ),
            )

    async with stage(Partial(), owner=object()) as ui:
        assert any("a" * 2100 in text for text in ui.texts())
        await ui.press(ui.key_for("reveal"))
        assert any("c" * 2100 in text for text in ui.texts())
        assert not ui.root.presentation.pending_reveals
        assert ui.root.plan is not None
        assert all(pager.pages is None for pager in ui.root.plan.scene.pagers)


async def test_readonly_action_cannot_queue_a_reveal() -> None:
    runtime = ComponentRuntime(Rows())
    runtime.commit(runtime.render())
    scope = runtime.presentation_cells.for_key("reveal")
    assert scope is not None
    with scope.activate(), readonly_transaction() as tx:
        event = PressEvent(Actor("reader"), RecordingResponder(), context=object(), _transaction=tx)
        with pytest.raises(RuntimeError, match="read-only actions"):
            event.reveal(sl.element_ref(sl.semantic.ActionControl, "row-2"))
    assert not runtime.presentation.pending_reveals
    runtime.finish()
