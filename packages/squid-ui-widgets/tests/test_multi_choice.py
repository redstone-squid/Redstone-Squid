"""Staged cross-page selection and explicit commit behaviour."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
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
    selection = next(request for request in routes if request.action == "select:roles")
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
