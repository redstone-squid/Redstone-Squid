"""Cross-page selection with explicit Apply and immediate commit modes."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.ownership import Controlled
from squid_ui.semantic import ActionControl, Choices, FormTrigger, RoutedActionControl, RoutedChoices
from squid_ui_widgets import testing as wt


def _options(prefix: str, count: int) -> tuple[sl.semantic.Choice, ...]:
    return tuple(sl.choice(f"{prefix} {index}", key=f"{prefix}-{index}") for index in range(count))


def test_group_keys_are_validated_as_action_segments() -> None:
    with pytest.raises(ValueError, match=r"MultiChoiceGroup.key must not contain ':'"):
        sp.MultiChoiceGroup("bad:key", "Bad", _options("bad", 1))

    with pytest.raises(ValueError, match=r"MultiChoiceGroup.exclusive_with must not contain ':'"):
        sp.MultiChoiceGroup("good", "Good", _options("good", 1), exclusive_with=("bad:key",))


def test_machine_key_segment_is_a_string_value_with_one_delimiter_rule() -> None:
    segment = sp.MachineKeySegment("profile.links")

    assert isinstance(segment, str)
    assert segment == "profile.links"
    with pytest.raises(ValueError, match="must not be empty"):
        sp.MachineKeySegment("")


def test_page_actions_reject_unknown_directions_and_extra_segments() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 30)),),
    )

    assert pattern.transition(pattern.initial_state, "page:roles:sideways") == pattern.initial_state
    assert pattern.transition(pattern.initial_state, "page:roles:next:extra") == pattern.initial_state


async def test_window_merge_preserves_staging_from_other_pages() -> None:
    panel = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("members", "Members", _options("member", 30)),),
        key="roles",
        maximum=10,
    ).build_component()
    harness = wt.driving(panel)

    await harness.choose("roles.members.select", "member-0", "member-1")
    await harness.press("roles.members.next")
    await harness.choose("roles.members.select", "member-25", "member-26")

    assert harness.state.staged == ("member-0", "member-1", "member-25", "member-26")


def test_exclusive_group_pick_clears_its_rivals_symmetrically() -> None:
    pattern = sp.MultiChoice(
        "Access",
        (
            sp.MultiChoiceGroup("roles", "Roles", _options("role", 2), exclusive_with=("everyone",)),
            sp.MultiChoiceGroup("everyone", "Everyone", _options("all", 1)),
        ),
    )
    state = pattern.transition(pattern.initial_state, "select:roles", values=("role-0", "role-1"))
    state = pattern.transition(state, "select:everyone", values=("all-0",))
    assert state.staged == ("all-0",)

    state = pattern.transition(state, "select:roles", values=("role-1",))
    assert state.staged == ("role-1",)


def test_cardinality_violation_blocks_apply_and_reduces_other_window_capacity() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (
            sp.MultiChoiceGroup("left", "Left", _options("left", 4)),
            sp.MultiChoiceGroup("right", "Right", _options("right", 4)),
        ),
        maximum=3,
    )
    invalid = sp.MultiChoiceState(("left-0", "left-1", "right-0", "right-1"))
    component = pattern.build_component(initial=invalid)
    rendered = component.render()
    apply = next(
        node
        for node in engine.walk(rendered)
        if isinstance(node, sl.semantic.ActionControl) and node.key == "choices.apply"
    )
    assert not apply.available
    assert pattern.errors(invalid) == ("Select no more than 3 options.",)

    staged_elsewhere = sp.MultiChoiceState(("right-0", "right-1"))
    rendered = pattern.build_component(initial=staged_elsewhere).render()
    left = next(
        node for node in engine.walk(rendered) if isinstance(node, Choices) and node.key == "choices.left.select"
    )
    assert left.maximum == 1


async def test_apply_commits_and_dispatches_exactly_once() -> None:
    commits: list[tuple[str, ...]] = []

    async def applied(_event: sp.TransitionEvent[sp.MultiChoiceState], values: tuple[str, ...]) -> None:
        commits.append(values)

    panel = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        minimum=1,
    ).build_component(on_commit=applied)
    harness = wt.driving(panel)

    await harness.choose("choices.roles.select", "role-1")
    await harness.press("choices.apply")

    assert harness.state.committed == ("role-1",)
    assert commits == [("role-1",)], "applying twice would append a second entry"
    assert not harness.control("choices.apply").available, "nothing left to apply"


def test_small_panel_offers_a_modal_alternate_with_staged_prefill() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
    )
    rendered = pattern.build_component(initial=sp.MultiChoiceState(("role-1",))).render()
    alternate = next(node for node in engine.walk(rendered) if isinstance(node, FormTrigger))
    assert alternate.spec.prefill == {"selection": ("role-1",)}
    field = alternate.spec.items[0]
    assert isinstance(field, sl.forms.MultiChoiceField)


def test_panel_modal_alternate_scales_to_twenty_five_options() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 25)),),
    )

    rendered = pattern.build_component().render()

    assert any(isinstance(node, FormTrigger) for node in engine.walk(rendered))


def test_router_shell_encodes_page_and_apply_state_and_uses_input_for_selection() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 30)),),
        maximum=5,
    )
    routes: list[sp.TransitionRoute[sp.MultiChoiceState]] = []

    def route(request: sp.TransitionRoute[sp.MultiChoiceState]) -> str:
        routes.append(request)
        return f"pick:{len(routes)}"

    rendered = sp.RouteDriver(route).render(pattern, pattern.initial_state)
    assert any(isinstance(node, RoutedChoices) for node in engine.walk(rendered))
    selection = next(request for request in routes if request.action.startswith("select_window:roles:"))
    assert selection.phase == "input"

    next_page = next(request for request in routes if request.action == "page:roles:next")
    assert next_page.state.pages == (("roles", 1),)
    staged = sp.MultiChoiceState(("role-0",), (), ())
    routes.clear()
    rendered = sp.RouteDriver(route).render(pattern, staged)
    assert any(isinstance(node, RoutedActionControl) and node.key == "choices.apply" for node in engine.walk(rendered))
    apply = next(request for request in routes if request.action == "apply")
    assert apply == sp.TransitionRoute("apply", sp.MultiChoiceState(("role-0",), ("role-0",), ()), "next")


async def test_immediate_policy_commits_valid_changes_without_apply() -> None:
    commits: list[tuple[str, ...]] = []

    async def committed(_event: sp.TransitionEvent[sp.MultiChoiceState], values: tuple[str, ...]) -> None:
        commits.append(values)

    panel = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        minimum=1,
        commit=sp.CommitMode.IMMEDIATE,
    ).build_component(on_commit=committed)
    harness = wt.driving(panel)

    await harness.choose("choices.roles.select", "role-1")

    assert harness.state == sp.MultiChoiceState(("role-1",), ("role-1",))
    assert commits == [("role-1",)]
    assert engine.find_all(harness.nodes, ActionControl, key="choices.apply") == (), "no Apply under IMMEDIATE"


def test_immediate_policy_retains_invalid_staging_until_next_valid_change() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        maximum=1,
        commit=sp.CommitMode.IMMEDIATE,
    )

    invalid = pattern.transition(pattern.initial_state, "select:roles", values=("role-0", "role-1"))
    valid = pattern.transition(invalid, "select:roles", values=("role-1",))

    assert invalid.staged == ("role-0", "role-1")
    assert invalid.committed == ()
    assert valid.staged == valid.committed == ("role-1",)


def test_immediate_modal_submission_commits_in_one_transition() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        commit=sp.CommitMode.IMMEDIATE,
    )

    state = pattern.transition(pattern.initial_state, "modal", submitted={"selection": ("role-2",)})

    assert state.staged == state.committed == ("role-2",)


@pytest.mark.parametrize("commit", [sp.CommitMode.EXPLICIT, sp.CommitMode.IMMEDIATE])
async def test_exclusive_replacement_is_offered_at_capacity(commit: sp.CommitMode) -> None:
    pattern = sp.MultiChoice(
        "Access",
        (
            sp.MultiChoiceGroup("roles", "Roles", _options("role", 2), exclusive_with=("everyone",)),
            sp.MultiChoiceGroup("everyone", "Everyone", _options("all", 1)),
        ),
        committed=("role-0", "role-1"),
        maximum=2,
        commit=commit,
    )
    harness = wt.mounted(pattern)
    picker = next(
        node
        for node in engine.walk(harness.nodes)
        if isinstance(node, Choices) and node.key == "choices.everyone.select"
    )
    assert picker.maximum == 1

    routed = wt.routed(pattern)
    route = next(route for route in routed.routes if route.action.startswith("select_window:everyone:"))
    await harness.choose("choices.everyone.select", "all-0")
    assert pattern.transition(route.state, route.action, values=("all-0",)) == harness.state
    assert harness.state.staged == ("all-0",)
    assert harness.state.committed == (("all-0",) if commit is sp.CommitMode.IMMEDIATE else ("role-0", "role-1"))


async def test_delayed_mounted_submission_replaces_the_rendered_window() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 4)),),
        committed=("role-0", "role-2"),
        window_size=2,
    )
    harness = wt.mounted(pattern)
    old_nodes = harness.nodes
    await harness.press("choices.roles.next")

    # Dispatch the old picker after a page turn, as a delayed interaction would.
    picker = engine.find(old_nodes, Choices, key="choices.roles.select")
    assert isinstance(picker.selection, Controlled)
    await picker.selection.on_change(engine.choice_event(responder=harness.responder))
    assert harness.state.staged == ("role-2",)
    assert harness.state.pages == (("roles", 1),)
    assert harness.state.committed == ("role-0", "role-2")


def test_routed_window_survives_reconstruction_and_preserves_other_pages() -> None:
    def machine() -> sp.MultiChoice:
        return sp.MultiChoice(
            "Roles",
            (sp.MultiChoiceGroup("roles", "Roles", _options("role", 4)),),
            committed=("role-0", "role-2"),
            window_size=2,
        )

    original = machine()
    page_two = original.transition(original.initial_state, "page:roles:next")
    rendered = wt.routed(original, page_two)
    route = next(route for route in rendered.routes if route.action.startswith("select_window:roles:"))

    restored = machine()
    driver = sp.RouteDriver[sp.MultiChoiceState](lambda _request: "unused")
    replaced = driver.transition(restored, route.state, route.action, values=("role-3",))
    cleared = driver.transition(restored, route.state, route.action, values=())
    assert replaced.staged == ("role-0", "role-3")
    assert cleared.staged == ("role-0",)
    assert replaced.committed == cleared.committed == ("role-0", "role-2")


@pytest.mark.parametrize("change", ["reorder", "remove", "unavailable", "window_size"])
def test_changed_window_definition_rejects_old_selection(change: str) -> None:
    entries = _options("role", 4)
    original = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", entries),),
        committed=("role-0", "role-2"),
        window_size=2,
    )
    route = next(route for route in wt.routed(original).routes if route.action.startswith("select_window:roles:"))
    if change == "reorder":
        entries = tuple(reversed(entries))
    elif change == "remove":
        entries = entries[1:]
    elif change == "unavailable":
        entries = (sl.choice("Role 0", key="role-0", available=False), *entries[1:])
    restored = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", entries),),
        window_size=3 if change == "window_size" else 2,
    )
    assert restored.transition(route.state, route.action, values=("role-1",)) == route.state


async def test_available_options_define_windows_and_clamp_the_displayed_page() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (
            sp.MultiChoiceGroup(
                "roles",
                "Roles",
                (
                    sl.choice("Unavailable", key="hidden", available=False),
                    *_options("role", 3),
                ),
            ),
        ),
        window_size=2,
    )
    harness = wt.mounted(pattern)
    first = next(node for node in engine.walk(harness.nodes) if isinstance(node, Choices))
    assert tuple(choice.key for choice in first.choices) == ("role-0", "role-1")
    await harness.press("choices.roles.next")
    await harness.choose("choices.roles.select", "role-2")
    assert harness.state.staged == ("role-2",)

    clamped = wt.mounted(pattern, initial=sp.MultiChoiceState(pages=((sp.MachineKeySegment("roles"), 99),)))
    assert "Page 2 of 2" in " ".join(clamped.texts())
    await clamped.press("choices.roles.previous")
    assert clamped.state.pages == (("roles", 0),)


def test_no_available_options_renders_without_a_picker() -> None:
    pattern = sp.MultiChoice(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", (sl.choice("Hidden", key="hidden", available=False),)),),
    )
    for nodes in (wt.mounted(pattern).nodes, wt.routed(pattern).nodes):
        assert not any(isinstance(node, Choices | RoutedChoices) for node in engine.walk(nodes))
        assert "No available options." in engine.texts(nodes)
