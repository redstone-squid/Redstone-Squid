"""Editable collections retain only route-serializable form-value mappings."""

from collections.abc import Iterable, Mapping

import discord

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui_discord import Everyone, Mount
from squid_ui_discord.testing import commit_render, fake_interaction
from squid_ui.semantic import Actions, RoutedAction, Stack


def _form() -> sl.forms.FormSpec:
    return sl.forms.FormSpec("Entry", (sl.forms.TextField(key="name", label="Name"),))


def _editor(*, minimum: int = 0, maximum: int | None = None, window_size: int = 25) -> sp.CollectionEditor:
    return sp.CollectionEditor(
        "Links",
        create=_form(),
        label=lambda values: str(values["name"]),
        minimum=minimum,
        maximum=maximum,
        window_size=window_size,
    )


def _walk(node: object) -> Iterable[object]:
    yield node
    if isinstance(node, Stack):
        for child in node.children:
            yield from _walk(child)
    elif isinstance(node, Actions):
        yield from node.items


def _text_input(modal: discord.ui.Modal) -> discord.ui.TextInput:
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    return label.component


async def _submit(mount: Mount, action: str, value: str) -> discord.ui.Modal:
    opened = fake_interaction()
    await mount.dispatch(action, opened)
    modal = opened.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    _text_input(modal)._value = value  # pyrefly: ignore[missing-attribute]
    await modal.on_submit(fake_interaction())
    return modal


async def test_add_appends_a_minted_entry_and_reports_the_full_ordered_collection() -> None:
    changes: list[tuple[Mapping[str, object], ...]] = []

    async def changed(_event: sp.TransitionEvent[sp.CollectionState], values: tuple[Mapping[str, object], ...]) -> None:
        changes.append(values)

    component = _editor().build_component(on_change=changed)
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await _submit(mount, "collection.add", "OpenAI")

    assert component.machine_state == sp.CollectionState((sp.CollectionEntry("1", (("name", "OpenAI"),)),), "1")
    assert tuple(dict(value) for value in changes[-1]) == ({"name": "OpenAI"},)


async def test_edit_prefills_and_retains_the_entry_identity() -> None:
    editor = _editor()
    component = editor.build_component(initial=editor.initial_from(({"name": "Old"},)))
    component.machine_state = editor.transition(component.machine_state, "select", values=("1",))
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    modal = await _submit(mount, "collection.edit", "New")

    assert _text_input(modal).default == "Old"
    assert component.machine_state.entries == (sp.CollectionEntry("1", (("name", "New"),)),)


def test_remove_and_add_are_gated_by_minimum_and_maximum() -> None:
    editor = _editor(minimum=1, maximum=1)
    state = editor.initial_from(({"name": "Only"},))
    selected = editor.transition(state, "select", values=("1",))

    assert editor.transition(selected, "remove") is selected
    assert editor.transition(selected, "add", submitted={"name": "Extra"}) is selected
    assert editor.form_for(selected, "add") is None

    rendered = editor.build_component(initial=selected).render()
    add = next(
        item for item in _walk(rendered) if isinstance(item, sl.semantic.Action) and item.key == "collection.add"
    )
    remove = next(
        item for item in _walk(rendered) if isinstance(item, sl.semantic.Action) and item.key == "collection.remove"
    )
    assert not add.available
    assert not remove.available


def test_reorder_moves_the_selected_entry_up_and_down() -> None:
    editor = _editor()
    state = editor.initial_from(({"name": "A"}, {"name": "B"}, {"name": "C"}))
    state = editor.transition(state, "select", values=("2",))

    up = editor.transition(state, "up")
    down = editor.transition(up, "down")

    assert [entry.key for entry in up.entries] == ["2", "1", "3"]
    assert down == state


def test_window_pages_past_twenty_five_and_clamps_after_removal() -> None:
    editor = _editor(window_size=25)
    state = editor.initial_from({"name": f"Entry {index}"} for index in range(26))

    second = editor.transition(state, "page:next")

    assert second.page == 1
    rendered = editor.build_component(initial=second).render()
    picker = next(item for item in _walk(rendered) if isinstance(item, sl.semantic.Choices))
    assert [choice.label for choice in picker.choices] == ["Entry 25"]


def test_errors_report_invalid_initial_cardinality() -> None:
    editor = _editor(minimum=2, maximum=3)

    assert editor.errors(editor.initial_state) == ("Add at least 2 entries.",)
    too_many = editor.initial_from({"name": str(index)} for index in range(4))
    assert editor.errors(too_many) == ("Keep no more than 3 entries.",)


def test_custom_identity_and_routed_form_parity() -> None:
    editor = sp.CollectionEditor(
        "Links",
        create=_form(),
        label=lambda values: str(values["name"]),
        identity=lambda values: str(values["name"]).casefold(),
    )
    state = editor.initial_from(({"name": "Docs"},))
    state = editor.transition(state, "select", values=("docs",))
    routes: list[sp.TransitionRoute[sp.CollectionState]] = []

    def route(request: sp.TransitionRoute[sp.CollectionState]) -> str:
        routes.append(request)
        return f"collection:{request.action}"

    rendered = sp.RouteDriver(route).render(editor, state)

    assert editor.form_for(state, "edit") is not None
    assert any(isinstance(item, RoutedAction) and item.key == "collection.edit" for item in _walk(rendered))
    assert sp.TransitionRoute("edit", state, "input") in routes
