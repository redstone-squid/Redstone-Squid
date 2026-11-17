"""Declared presentation cells recover through snapshots and scoped storage."""

import json
from dataclasses import dataclass
from typing import Any, override

import pytest

import squid_ui as sl
from squid_reactivity import transaction
from squid_storage import MemoryScopedStore, Slot, json_codec
from squid_ui.authoring.semantic import Choices
from squid_ui.ext.discord import Everyone, MessageRoot
from squid_ui.ext.discord.durability import ComponentRegistry, MessageRootStateCodec, MessageRootStateError
from squid_ui.ext.discord.testing import commit_render, stage
from squid_ui.runtime import ComponentRuntime, history
from squid_ui.testing import find, press_event


class DurablePanel(sl.Component):
    edits = history()
    enabled = sl.presentation.toggle("Enabled", persist=True, record=edits)
    chosen = sl.presentation.choices(persist=True)
    transient = sl.presentation.toggle("Transient")

    @override
    def render(self) -> tuple[sl.LayoutNode, ...]:
        return (
            sl.action_controls(self.enabled.bind, self.transient.bind, key="controls"),
            self.chosen.bind(sl.choice("Alpha", key="a"), sl.choice("Beta", key="b")),
        )


async def test_dispatched_controls_and_application_actions_share_the_placement() -> None:
    class Consumer(DurablePanel):
        chosen = sl.presentation.choices(minimum=0, maximum=2, persist=True)
        received: tuple[str, ...] = sl.state(())

        @override
        def render(self) -> tuple[sl.LayoutNode, ...]:
            return (
                *super().render(),
                sl.action_controls(sl.action_control("Consume", self.consume, key="consume"), key="actions"),
            )

        async def consume(self, event: sl.PressEvent) -> None:
            self.received = event.read(self.chosen)

    component = Consumer()
    async with stage(component, owner=component) as ui:
        await ui.select("chosen", ("b",))
        await ui.press("consume")
        await ui.press("enabled")
        assert component.received == ("b",)
        assert ui.root.runtime.presentation_cells.export()["$"]["enabled"] is True
        assert len(component.edits.entries) == 1


async def test_snapshot_restores_values_with_empty_history_before_first_render() -> None:
    registry = ComponentRegistry()
    registry.register("panel", version=1, factory=DurablePanel)
    root = MessageRoot(DurablePanel(), access=Everyone(), timeout=None)
    commit_render(root)
    assert root.plan is not None
    with transaction():
        await root.plan.bindings["enabled"].handler(press_event())
    commit_render(root)
    assert isinstance(root.component, DurablePanel) and root.component.edits.can_undo
    snapshot = registry.capture(root, "panel")
    assert snapshot.presentation.declared == {"$": {"enabled": True, "chosen": ()}}
    encoded = MessageRootStateCodec.dumps(snapshot)
    recovered = registry.restore(MessageRootStateCodec.loads(encoded), access=Everyone(), timeout=None)
    commit_render(recovered)
    assert recovered.runtime.presentation_cells.export() == snapshot.presentation.declared
    assert isinstance(recovered.component, DurablePanel) and not recovered.component.edits.can_undo
    assert encoded == MessageRootStateCodec.dumps(MessageRootStateCodec.loads(encoded))


def test_old_snapshots_default_to_empty_declared_state_and_new_protocols_fail() -> None:
    registry = ComponentRegistry()
    registry.register("panel", version=1, factory=DurablePanel)
    root = MessageRoot(DurablePanel(), access=Everyone(), timeout=None)
    commit_render(root)
    raw = json.loads(MessageRootStateCodec.dumps(registry.capture(root, "panel")))
    raw["presentation"].pop("declared")
    raw["presentation"].pop("declared_protocol")
    decoded = MessageRootStateCodec.loads(json.dumps(raw))
    assert not decoded.presentation.declared
    recovered = registry.restore(decoded, access=Everyone(), timeout=None)
    commit_render(recovered)
    assert recovered.runtime.presentation_cells.export()["$"]["enabled"] is False
    raw["presentation"]["declared_protocol"] = 2
    with pytest.raises(MessageRootStateError):
        MessageRootStateCodec.loads(json.dumps(raw))


async def test_scoped_store_encodes_values_and_restores_choice_tuple_shape() -> None:
    model = DurablePanel()
    runtime = ComponentRuntime(model)
    tree = runtime.render()
    runtime.commit(tree)
    scope = runtime.presentation_cells.placement(model, "$")
    chosen = find(tree.nodes, Choices, key="chosen")
    assert isinstance(chosen.selection, sl.ValueBinding)
    with scope.activate():
        await chosen.selection.commit(press_event(), ("b",))
    store = MemoryScopedStore()
    slot = Slot[str, dict[str, dict[str, Any]]]("presentation", json_codec())
    await store.put(slot, "first-message", runtime.presentation_cells.export())
    payload = await store.get(slot, "first-message")
    assert payload is not None
    recovered = ComponentRuntime(DurablePanel())
    recovered.presentation_cells.restore(payload)
    recovered.commit(recovered.render())
    assert recovered.presentation_cells.export()["$"]["chosen"] == ("b",)
    assert await store.get(slot, "another-message") is None


@dataclass(frozen=True)
class PriceRange:
    minimum: int
    maximum: int


class CustomPanel(sl.Component):
    price = sl.presentation(PriceRange(10, 20), persist=True)

    @override
    def render(self):
        return sl.paragraph(f"{self.price.value.minimum}-{self.price.value.maximum}")


def test_custom_persistent_values_require_and_use_explicit_recipe_codecs() -> None:
    root = MessageRoot(CustomPanel(), access=Everyone(), timeout=None)
    commit_render(root)
    plain = ComponentRegistry()
    plain.register("price", version=1, factory=CustomPanel)
    with pytest.raises(MessageRootStateError, match="not JSON serializable"):
        plain.capture(root, "price")

    def encode(path: str, name: str, value: PriceRange) -> str:
        return json.dumps([value.minimum, value.maximum])

    def decode(path: str, name: str, value: str) -> PriceRange:
        minimum, maximum = json.loads(value)
        return PriceRange(minimum, maximum)

    registry = ComponentRegistry()
    registry.register("price", version=1, factory=CustomPanel, presentation_encode=encode, presentation_decode=decode)
    snapshot = MessageRootStateCodec.loads(MessageRootStateCodec.dumps(registry.capture(root, "price")))
    recovered = registry.restore(snapshot, access=Everyone(), timeout=None)
    commit_render(recovered)
    assert recovered.runtime.presentation_cells.export()["$"]["price"] == PriceRange(10, 20)
