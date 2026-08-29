"""Form and nested-pattern sections under one editor commit boundary."""

from collections.abc import Iterable, Mapping
from typing import Any, cast

import squid_layouts as sl
from squid_layouts.semantic import Actions, FormTrigger, RoutedAction, Stack


def _walk(node: object) -> Iterable[object]:
    yield node
    if isinstance(node, Stack):
        for child in node.children:
            yield from _walk(child)
    elif isinstance(node, Actions):
        yield from node.items


def _profile_section() -> sl.EditorSection[tuple[tuple[str, object], ...], Mapping[str, object]]:
    return sl.EditorSection.form(
        "profile",
        "Profile",
        sl.FormSpec(
            "Profile",
            (
                sl.TextField(key="name", label="Name"),
                sl.TextField(key="bio", label="Bio", required=False),
            ),
        ),
    )


def test_form_section_prefills_stages_and_explicitly_commits() -> None:
    section = _profile_section()
    editor = sl.Editor("Account", (section,))
    initial = editor.initial_from({"profile": {"name": "Old", "bio": None}})

    form = editor.form_for(initial, "submit:profile")
    assert form is not None
    assert form.prefill == {"name": "Old", "bio": None}

    staged = editor.transition(initial, "submit:profile", submitted={"name": "New", "bio": "Hello"})
    committed = editor.transition(staged, "save")

    assert editor.dirty_sections(staged) == frozenset({"profile"})
    assert section.value(staged) == {"name": "New", "bio": "Hello"}
    assert editor.dirty_sections(committed) == frozenset()
    assert editor.committed_values(committed)["profile"] == {"name": "New", "bio": "Hello"}


async def test_immediate_commit_reports_complete_values_and_all_changed_keys() -> None:
    commits: list[tuple[sl.EditorValues, frozenset[str]]] = []

    async def committed(
        _event: sl.PatternEvent[sl.EditorState], values: sl.EditorValues, changed: frozenset[str]
    ) -> None:
        commits.append((values, changed))

    editor = sl.Editor("Account", (_profile_section(),), commit=sl.CommitPolicy.IMMEDIATE)
    component = editor.component(
        initial={"profile": {"name": "Old", "bio": None}},
        on_commit=committed,
    )
    state = editor.transition(
        component.pattern_state,
        "submit:profile",
        submitted={"name": "New", "bio": None},
    )
    previous = component.pattern_state
    component.pattern_state = state
    assert component.on_change is not None
    await component.on_change(sl.PatternEvent(cast(Any, object()), "submit:profile", previous, state))

    assert commits[-1][0]["profile"] == {"name": "New", "bio": None}
    assert commits[-1][1] == frozenset({"profile"})


def test_invalid_immediate_edit_stays_staged_until_aggregate_becomes_valid() -> None:
    def validate(values: sl.EditorValues) -> tuple[sl.FormIssue, ...]:
        profile = cast(Mapping[str, object], values["profile"])
        return () if profile["name"] else (sl.FormError("Name is required."),)

    editor = sl.Editor(
        "Account",
        (_profile_section(),),
        commit=sl.CommitPolicy.IMMEDIATE,
        validate=validate,
    )
    initial = editor.initial_from({"profile": {"name": "Old", "bio": None}})
    invalid = editor.transition(initial, "submit:profile", submitted={"name": "", "bio": None})
    valid = editor.transition(invalid, "submit:profile", submitted={"name": "New", "bio": None})

    assert editor.dirty_sections(invalid) == frozenset({"profile"})
    assert editor.committed_values(invalid)["profile"] == {"name": "Old", "bio": None}
    assert editor.dirty_sections(valid) == frozenset()


def _collection_section() -> tuple[
    sl.CollectionEditor,
    sl.EditorSection[sl.CollectionState, tuple[Mapping[str, object], ...]],
]:
    collection = sl.CollectionEditor(
        "Links",
        create=sl.FormSpec("Link", (sl.TextField(key="name", label="Name"),)),
        label=lambda value: str(value["name"]),
        window_size=1,
    )
    section = sl.EditorSection.pattern(
        "links",
        "Links",
        collection,
        load=lambda value: collection.initial_from(cast(Iterable[Mapping[str, object]], value)),
        dump=collection.values,
        summary=lambda value: f"{len(value)} links",
        issues=lambda state: (sl.FormError(message) for message in collection.errors(state)),
    )
    return collection, section


def test_nested_pattern_navigation_is_not_dirty_but_value_changes_are() -> None:
    _collection, section = _collection_section()
    editor = sl.Editor("Account", (section,))
    state = editor.initial_from({"links": ({"name": "A"}, {"name": "B"})})
    state = editor.transition(state, "edit:links")

    paged = editor.transition(state, "section:links:page:next")
    added = editor.transition(paged, "section:links:add", submitted={"name": "C"})

    assert paged.editing == "links"
    assert editor.dirty_sections(paged) == frozenset()
    assert editor.dirty_sections(added) == frozenset({"links"})
    assert tuple(dict(value) for value in cast(tuple[Mapping[str, object], ...], section.value(added))) == (
        {"name": "A"},
        {"name": "B"},
        {"name": "C"},
    )


def test_nested_forms_keep_router_shell_parity() -> None:
    _collection, section = _collection_section()
    editor = sl.Editor("Account", (section,))
    state = editor.transition(editor.initial_state, "edit:links")
    routes: list[sl.PatternRoute[sl.EditorState]] = []

    def route(request: sl.PatternRoute[sl.EditorState]) -> str:
        routes.append(request)
        return f"editor:{len(routes)}"

    rendered = sl.RouterShell(route).render(editor, state)

    add = next(node for node in _walk(rendered) if isinstance(node, RoutedAction) and node.key.endswith("links.add"))
    assert add
    request = next(route for route in routes if route.action == "section:links:add")
    assert request.phase == "input"
    assert editor.form_for(state, "section:links:add") is not None


def test_editor_render_shows_unsaved_status_and_gates_save_on_validation() -> None:
    editor = sl.Editor(
        "Account",
        (_profile_section(),),
        validate=lambda _values: (sl.FormError("Not ready"),),
    )
    staged = editor.transition(
        editor.initial_from({"profile": {"name": "Old", "bio": None}}),
        "submit:profile",
        submitted={"name": "New", "bio": None},
    )

    rendered = editor.component(initial=staged).render()
    save = next(node for node in _walk(rendered) if isinstance(node, sl.Action) and node.key == "editor.save")

    assert not save.available
    assert any(isinstance(node, FormTrigger) for node in _walk(rendered))
