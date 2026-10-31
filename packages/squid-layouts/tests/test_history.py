"""Undo: what the framework restores, what the author reverses, and where each stops."""

import pytest

from squid_layouts import (
    Action,
    Component,
    History,
    HistoryError,
    ReactiveWriteError,
    history,
    history_actions,
    state,
    transaction,
)
from squid_layouts.primitives import Text
from squid_layouts.runtime import ComponentRuntime
from squid_layouts.runtime.reactivity import readonly_transaction


class World:
    """The half the framework cannot restore: a database, an API, anything outside the tree."""

    def __init__(self, channel: int | None = None) -> None:
        self.channel = channel
        self.calls: list[str] = []

    async def set_channel(self, channel: int | None) -> None:
        self.calls.append(f"set:{channel}")
        self.channel = channel


class Panel(Component):
    history: History = history(limit=3)
    channel: int | None = state(None)
    page: int = state(1)
    build: object = state(None, copy="ref")

    def __init__(self, world: World) -> None:
        self.world = world

    def render(self):
        return Text(str(self.channel))

    async def set_channel(self, channel: int | None) -> None:
        previous = self.channel
        await self.world.set_channel(channel)
        self.channel = channel
        self.history.record("channel", undo=lambda: self.world.set_channel(previous))


def attached[ComponentT: Component](component: ComponentT) -> ComponentT:
    """Give a component a runtime, which is what makes its writes state changes."""
    ComponentRuntime(component)
    return component


def panel() -> Panel:
    return attached(Panel(World()))


def controls(stack: History) -> tuple[Action, Action]:
    undo, redo = history_actions(stack).actions
    assert isinstance(undo, Action)
    assert isinstance(redo, Action)
    return undo, redo


class TestRecording:
    def test_needs_an_action_in_flight(self):
        stack = panel().history
        with pytest.raises(RuntimeError, match="inside an action's transaction"):
            stack.record("nothing")

    def test_a_read_only_action_has_nothing_to_record(self):
        stack = panel().history
        with pytest.raises(ReactiveWriteError, match="changed nothing"), readonly_transaction():
            stack.record("nothing")

    def test_a_failed_action_records_nothing(self):
        subject = panel()
        with pytest.raises(RuntimeError), transaction():
            subject.channel = 7
            subject.history.record("channel")
            message = "the handler failed after recording"
            raise RuntimeError(message)
        assert subject.history.entries == ()
        assert subject.channel is None

    def test_one_entry_describes_the_whole_action(self):
        subject = panel()
        with transaction():
            subject.history.record("first")
            with pytest.raises(HistoryError, match="already recorded"):
                subject.history.record("second")

    def test_redo_without_undo_is_refused(self):
        subject = panel()
        with transaction(), pytest.raises(TypeError, match="pass undo="):
            subject.history.record("channel", redo=lambda: subject.world.set_channel(1))

    def test_the_entry_covers_writes_made_after_the_call(self):
        subject = panel()
        with transaction():
            subject.history.record("channel")
            subject.channel = 7
        assert subject.history.entries[-1].delta.changes

    def test_the_limit_drops_the_oldest(self):
        subject = panel()
        for index in range(5):
            with transaction():
                subject.page = index
                subject.history.record(f"page {index}")
        assert [entry.label for entry in subject.history.entries] == ["page 2", "page 3", "page 4"]


class TestUndo:
    async def test_it_restores_only_what_the_action_wrote(self):
        subject = panel()
        with transaction():
            subject.channel = 7
            subject.history.record("channel")
        with transaction():
            subject.page = 4
        await subject.history.undo()
        assert subject.channel is None
        assert subject.page == 4

    async def test_an_empty_stack_is_not_an_error(self):
        assert await panel().history.undo() is None

    async def test_the_world_goes_first(self):
        subject = panel()
        order: list[str] = []

        async def inverse() -> None:
            order.append(f"world, state was {subject.channel}")

        with transaction():
            subject.channel = 7
            subject.history.record("channel", undo=inverse)
        await subject.history.undo()
        assert order == ["world, state was 7"]
        assert subject.channel is None

    async def test_a_failed_inverse_keeps_the_entry_and_the_state(self):
        subject = panel()

        async def inverse() -> None:
            message = "the service rejected the reversal"
            raise RuntimeError(message)

        with transaction():
            subject.channel = 7
            subject.history.record("channel", undo=inverse)
        with pytest.raises(RuntimeError, match="rejected the reversal"):
            await subject.history.undo()
        assert subject.channel == 7
        assert [entry.label for entry in subject.history.entries] == ["channel"]

    async def test_an_inverse_may_not_write_component_state(self):
        subject = panel()

        async def inverse() -> None:
            subject.page = 99

        with transaction():
            subject.channel = 7
            subject.history.record("channel", undo=inverse)
        with pytest.raises(ReactiveWriteError, match="may not write component state"):
            await subject.history.undo()
        assert subject.page == 1
        assert subject.channel == 7

    async def test_the_real_shape_reverses_both_halves(self):
        world = World()
        subject = attached(Panel(world))
        # Two ordinary actions; the mount is what supplies the transaction in production.
        for channel in (4, 9):
            with transaction():
                await subject.set_channel(channel)
        assert world.channel == 9
        await subject.history.undo()
        assert world.channel == 4
        assert subject.channel == 4

    async def test_reference_copied_state_restores_the_reference(self):
        subject = panel()
        first, second = [1], [2]
        subject.build = first
        with transaction():
            subject.build = second
            subject.history.record("build")
        second.append(3)
        await subject.history.undo()
        assert subject.build is first
        # The known limit, pinned: in-place mutation of the object was never captured.
        assert second == [2, 3]


class TestRedo:
    async def test_a_framework_only_entry_replays_itself(self):
        subject = panel()
        with transaction():
            subject.page = 4
            subject.history.record("page")
        await subject.history.undo()
        assert subject.page == 1
        assert await subject.history.redo() is not None
        assert subject.page == 4
        assert subject.history.can_undo

    async def test_a_world_entry_without_an_inverse_is_dropped(self):
        subject = panel()
        with transaction():
            subject.channel = 7
            subject.history.record("channel", undo=lambda: subject.world.set_channel(None))
        await subject.history.undo()
        assert subject.history.redoable == ()
        assert not subject.history.can_redo

    async def test_a_world_entry_with_an_inverse_replays_both_halves(self):
        world = World()
        subject = attached(Panel(world))
        with transaction():
            subject.channel = 7
            subject.history.record(
                "channel",
                undo=lambda: world.set_channel(None),
                redo=lambda: world.set_channel(7),
            )
        await subject.history.undo()
        assert (world.channel, subject.channel) == (None, None)
        await subject.history.redo()
        assert (world.channel, subject.channel) == (7, 7)

    async def test_recording_clears_the_redo_stack(self):
        subject = panel()
        with transaction():
            subject.page = 4
            subject.history.record("page")
        await subject.history.undo()
        assert subject.history.can_redo
        with transaction():
            subject.page = 9
            subject.history.record("page again")
        assert not subject.history.can_redo


class TestControls:
    def test_availability_follows_the_stacks(self):
        subject = panel()
        undo, redo = controls(subject.history)
        assert (undo.available, redo.available) == (False, False)
        with transaction():
            subject.page = 2
            subject.history.record("page")
        undo, redo = controls(subject.history)
        assert (undo.available, redo.available) == (True, False)

    async def test_the_controls_drive_the_stack(self):
        subject = panel()
        with transaction():
            subject.page = 2
            subject.history.record("page")
        undo, _ = controls(subject.history)
        with transaction():
            await undo.on_trigger(None)  # type: ignore[bad-argument-type]
        assert subject.page == 1

    def test_labels_name_the_reversible_action(self):
        subject = panel()
        assert subject.history.undo_label is None
        with transaction():
            subject.page = 2
            subject.history.record("turned the page")
        assert subject.history.undo_label == "turned the page"


def test_each_instance_owns_its_stack():
    first, second = panel(), panel()
    assert first.history is not second.history
    assert first.history is first.history
