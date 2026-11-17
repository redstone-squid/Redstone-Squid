"""Presentation controls use placement-owned cells throughout rendering and undo."""

from typing import Any, override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
import squid_ui.ext.slack as slack
from squid_reactivity import transaction
from squid_ui.authoring.semantic import Toggle
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.interactions import ActionEvent, Actor, SelectionEvent
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.result import PlanResult
from squid_ui.planning.target import Target
from squid_ui.runtime import ComponentRuntime, history
from squid_ui.testing import RecordingResponder, find, press_event

TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(AdapterProfile(DiscordAdapter, "presentation.discord", "1")),
    html.target(),
    slack.message_target(adapter=AdapterProfile(SlackAdapter, "presentation.slack", "1")),
]


class Draft:
    def __init__(self) -> None:
        self.count = 0


class Panel(sl.Component):
    edits = history()
    flag = sl.presentation.toggle("Flag")
    tracked = sl.presentation.toggle("Tracked", record=edits, persist=True)
    selected = sl.presentation.choices(minimum=0, maximum=2, persist=True)
    draft = sl.presentation(factory=Draft, opaque=True)
    consumed: tuple[str, ...] = sl.state(())

    @override
    def render(self):
        return (
            sl.paragraph(f"flag={self.flag.value}, selected={self.selected.value}"),
            sl.action_controls(
                self.flag.bind,
                self.tracked.bind,
                sl.action_control("Consume", self.consume, key="consume", record=self.edits),
                sl.action_control("Mutate", self.mutate, key="mutate"),
                key="actions",
            ),
            self.selected.bind(sl.choice("Alpha", key="a"), sl.choice("Beta", key="b")),
        )

    async def consume(self, event: ActionEvent) -> None:
        self.consumed = event.read(self.selected)

    async def mutate(self, event: ActionEvent) -> None:
        draft = event.read(self.draft)
        draft.count += 1
        self.mutated(draft)


class Pair(sl.Component):
    show_right: bool = sl.state(True)

    def __init__(self) -> None:
        self.panel = Panel()

    @override
    def render(self):
        return sl.group(self.panel.keyed("left"), self.show_right and self.panel.keyed("right"))


def delivered(runtime: ComponentRuntime, target: Target[Any, Any, Any, Any] = TARGETS[0]) -> PlanResult[Any]:
    tree = runtime.render()
    output = plan(tree.nodes, target=target, session=runtime.presentation)
    runtime.presentation.apply_updates(output.session_updates)
    runtime.commit(tree)
    return output


async def invoke(output: PlanResult[Any], key: str, values: tuple[str, ...] | None = None) -> None:
    event = (
        press_event()
        if values is None
        else SelectionEvent(Actor("1"), RecordingResponder(), context=None, values=values)
    )
    binding = output.bindings[key]
    with transaction() as tx:
        if binding.record is not None:
            binding.record.record(tx, binding.label)
        await binding.handler(event)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_controls_update_readers_and_actions_without_assignment_handlers(
    target: Target[Any, Any, Any, Any],
) -> None:
    panel = Panel()
    runtime = ComponentRuntime(panel)
    first = delivered(runtime, target)
    await invoke(first, "flag")
    assert runtime.dirty
    second = delivered(runtime, target)
    assert first.scene != second.scene
    await invoke(second, "selected", ("a",))
    third = delivered(runtime, target)
    await invoke(third, "consume")
    assert panel.consumed == ("a",)
    assert len(panel.edits.entries) == 1
    assert (await panel.edits.undo()).applied
    assert panel.consumed == ()
    assert runtime.presentation_cells.export() == {"$": {"tracked": False, "selected": ("a",)}}


async def test_history_restores_recorded_values_and_reacts_without_replaying_handlers() -> None:
    panel = Panel()
    runtime = ComponentRuntime(panel)
    output = delivered(runtime)
    await invoke(output, "tracked")
    assert panel.edits.can_undo
    assert runtime.presentation_cells.export()["$"]["tracked"] is True
    assert (await panel.edits.undo()).applied
    assert runtime.presentation_cells.export()["$"]["tracked"] is False
    assert (await panel.edits.redo()).applied
    assert runtime.presentation_cells.export()["$"]["tracked"] is True


async def test_shared_instance_placements_remain_independent_and_expire_history() -> None:
    pair = Pair()
    runtime = ComponentRuntime(pair)
    output = delivered(runtime)
    await invoke(output, "right.tracked")
    await invoke(output, "right.flag")
    nodes = runtime.render().nodes
    left = find(nodes, Toggle, key="left.flag")
    right = find(nodes, Toggle, key="right.flag")
    assert isinstance(left.on, sl.ValueBinding) and not left.on.value
    assert isinstance(right.on, sl.ValueBinding) and right.on.value
    assert pair.panel.edits.can_undo
    pair.show_right = False
    delivered(runtime)
    assert not pair.panel.edits.can_undo
    pair.show_right = True
    delivered(runtime)
    assert not runtime.presentation_cells.export()["right"]["tracked"]
    with pytest.raises(sl.LayoutInvariantError, match="no longer mounted"):
        await invoke(output, "right.flag")


async def test_failed_action_rolls_back_presentation_writes_and_history() -> None:
    panel = Panel()
    runtime = ComponentRuntime(panel)
    output = delivered(runtime)
    with pytest.raises(ValueError), transaction():
        await output.bindings["tracked"].handler(press_event())
        raise ValueError("failed after changing the presentation value")
    assert not panel.edits.can_undo
    assert not runtime.presentation_cells.export()["$"]["tracked"]


async def test_replacement_at_the_same_path_binds_the_new_owner() -> None:
    pair = Pair()
    pair.show_right = False
    runtime = ComponentRuntime(pair)
    first = delivered(runtime)
    pair.panel = Panel()
    pair.invalidate()
    replacement = delivered(runtime)
    await invoke(replacement, "left.consume")
    assert pair.panel.consumed == ()
    with pytest.raises(sl.LayoutInvariantError, match="no longer mounted"):
        await invoke(first, "left.consume")


async def test_opaque_notifications_share_existing_mutated_entry_point() -> None:
    panel = Panel()
    runtime = ComponentRuntime(panel)
    output = delivered(runtime)
    scope = runtime.presentation_cells.placement(panel, "$")
    draft = panel.draft.read(scope)
    with scope.activate(), transaction():
        draft.count += 1
        panel.mutated(draft)
    assert draft.count == 1
    assert not panel.edits.can_undo
    with scope.activate(), pytest.raises(ValueError), transaction():
        draft.count += 1
        panel.mutated(draft)
        raise ValueError
    assert draft.count == 2
    await invoke(output, "mutate")
    assert draft.count == 3


async def test_same_object_in_state_and_presentation_is_ambiguous() -> None:
    class Ambiguous(Panel):
        ordinary: Draft = sl.state(factory=Draft, opaque=True)

    panel = Ambiguous()
    runtime = ComponentRuntime(panel)
    delivered(runtime)
    scope = runtime.presentation_cells.placement(panel, "$")
    panel.ordinary = panel.draft.read(scope)
    with scope.activate(), pytest.raises(TypeError, match="more than one field"), transaction():
        panel.mutated(panel.ordinary)


def test_defaults_and_failed_candidates_do_not_share_placement_objects() -> None:
    pair = Pair()
    runtime = ComponentRuntime(pair)
    runtime.render()
    left = runtime.presentation_cells.placement(pair.panel, "left")
    right = runtime.presentation_cells.placement(pair.panel, "right")
    first = pair.panel.draft.read(left)
    assert first is not pair.panel.draft.read(right)
    runtime.discard()
    runtime.render()
    assert pair.panel.draft.read(runtime.presentation_cells.placement(pair.panel, "left")) is not first


def test_scope_errors_and_declaration_validation() -> None:
    panel = Panel()
    with pytest.raises(sl.LayoutInvariantError, match="during render"):
        _ = panel.flag.value
    with pytest.raises(RuntimeError, match="active presentation"):
        press_event().read(panel.flag)
    with pytest.raises(TypeError, match="cannot be persisted"):
        sl.presentation(factory=Draft, opaque=True, persist=True)
    with pytest.raises(TypeError, match="record_label"):
        sl.presentation.choices(record=history())
    with pytest.raises(TypeError, match="exactly one"):
        sl.presentation()  # type: ignore  # Exercise runtime validation of an invalid declaration.


def test_persistent_cells_restore_before_render_and_reject_stale_fields() -> None:
    panel = Panel()
    runtime = ComponentRuntime(panel)
    runtime.presentation_cells.restore({"$": {"selected": ["b"], "tracked": True}})
    delivered(runtime)
    assert runtime.presentation_cells.export() == {"$": {"selected": ("b",), "tracked": True}}
    assert not panel.edits.can_undo
    invalid = ComponentRuntime(Panel())
    invalid.presentation_cells.restore({"$": {"typo": True}})
    with pytest.raises(ValueError, match="unknown presentation fields"):
        invalid.render()
