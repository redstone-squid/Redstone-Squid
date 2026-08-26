"""Staged cross-page selection and explicit commit behavior."""

from collections.abc import Iterable

import discord

import squid_layouts as sl
import squid_patterns as sp
from squid_discord import Everyone, Mount
from squid_discord.testing import commit_render, fake_interaction
from squid_layouts.semantic import Actions, Choices, FallbackContent, FormTrigger, RoutedAction, RoutedChoices, Stack


def _options(prefix: str, count: int) -> tuple[sl.semantic.Choice, ...]:
    return tuple(sl.choice(f"{prefix} {index}", key=f"{prefix}-{index}") for index in range(count))


def _walk(node: object) -> Iterable[object]:
    yield node
    if isinstance(node, Stack):
        for child in node.children:
            yield from _walk(child)
    elif isinstance(node, FallbackContent):
        yield from _walk(node.primary)
        for alternate in node.alternates:
            yield from _walk(alternate)
    elif isinstance(node, Actions):
        yield from node.items


async def test_window_merge_preserves_staging_from_other_pages() -> None:
    panel = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("members", "Members", _options("member", 30)),),
        key="roles",
        maximum=10,
    ).build_component()
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("roles.members.select", fake_interaction(), ["member-0", "member-1"])
    await mount.dispatch("roles.members.next", fake_interaction())
    commit_render(mount)
    await mount.dispatch("roles.members.select", fake_interaction(), ["member-25", "member-26"])

    assert panel.pattern_state.staged == ("member-0", "member-1", "member-25", "member-26")


def test_exclusive_group_pick_clears_its_rivals_symmetrically() -> None:
    pattern = sp.MultiChoicePanel(
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
    pattern = sp.MultiChoicePanel(
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
        node for node in _walk(rendered) if isinstance(node, sl.semantic.Action) and node.key == "choices.apply"
    )
    assert not apply.available
    assert pattern.errors(invalid) == ("Select no more than 3 options.",)

    staged_elsewhere = sp.MultiChoiceState(("right-0", "right-1"))
    rendered = pattern.build_component(initial=staged_elsewhere).render()
    left = next(node for node in _walk(rendered) if isinstance(node, Choices) and node.key == "choices.left.select")
    assert left.maximum == 1


async def test_apply_commits_and_dispatches_exactly_once() -> None:
    commits: list[tuple[str, ...]] = []

    async def applied(_event: sp.PatternEvent[sp.MultiChoiceState], values: tuple[str, ...]) -> None:
        commits.append(values)

    panel = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        minimum=1,
    ).build_component(on_commit=applied)
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)
    await mount.dispatch("choices.roles.select", fake_interaction(), ["role-1"])
    commit_render(mount)
    await mount.dispatch("choices.apply", fake_interaction())

    assert panel.pattern_state.committed == ("role-1",)
    assert commits == [("role-1",)]
    view = commit_render(mount)
    apply = next(item for item in view.walk_children() if getattr(item, "label", None) == "Apply")
    assert isinstance(apply, discord.ui.Button)
    assert apply.disabled


def test_small_panel_offers_a_modal_alternate_with_staged_prefill() -> None:
    pattern = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
    )
    rendered = pattern.build_component(initial=sp.MultiChoiceState(("role-1",))).render()
    alternate = next(node for node in _walk(rendered) if isinstance(node, FormTrigger))
    assert alternate.spec.prefill == {"selection": ("role-1",)}
    field = alternate.spec.items[0]
    assert isinstance(field, sl.forms.MultiChoiceField)


def test_panel_modal_alternate_scales_to_twenty_five_options() -> None:
    pattern = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 25)),),
    )

    rendered = pattern.build_component().render()

    assert any(isinstance(node, FormTrigger) for node in _walk(rendered))


def test_router_shell_encodes_page_and_apply_state_and_uses_input_for_selection() -> None:
    pattern = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 30)),),
        maximum=5,
    )
    routes: list[sp.PatternRoute[sp.MultiChoiceState]] = []

    def route(request: sp.PatternRoute[sp.MultiChoiceState]) -> str:
        routes.append(request)
        return f"pick:{len(routes)}"

    rendered = sp.RouterShell(route).render(pattern, pattern.initial_state)
    assert any(isinstance(node, RoutedChoices) for node in _walk(rendered))
    selection = next(request for request in routes if request.action == "select:roles")
    assert selection.phase == "input"

    next_page = next(request for request in routes if request.action == "page:roles:next")
    assert next_page.state.pages == (("roles", 1),)
    staged = sp.MultiChoiceState(("role-0",), (), ())
    routes.clear()
    rendered = sp.RouterShell(route).render(pattern, staged)
    assert any(isinstance(node, RoutedAction) and node.key == "choices.apply" for node in _walk(rendered))
    apply = next(request for request in routes if request.action == "apply")
    assert apply == sp.PatternRoute("apply", sp.MultiChoiceState(("role-0",), ("role-0",), ()), "next")


async def test_immediate_policy_commits_valid_changes_without_apply() -> None:
    commits: list[tuple[str, ...]] = []

    async def committed(_event: sp.PatternEvent[sp.MultiChoiceState], values: tuple[str, ...]) -> None:
        commits.append(values)

    panel = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        minimum=1,
        commit=sp.CommitPolicy.IMMEDIATE,
    ).build_component(on_commit=committed)
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("choices.roles.select", fake_interaction(), ["role-1"])

    assert panel.pattern_state == sp.MultiChoiceState(("role-1",), ("role-1",))
    assert commits == [("role-1",)]
    assert not any(
        isinstance(node, sl.semantic.Action) and node.key == "choices.apply" for node in _walk(panel.render())
    )


def test_immediate_policy_retains_invalid_staging_until_next_valid_change() -> None:
    pattern = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        maximum=1,
        commit=sp.CommitPolicy.IMMEDIATE,
    )

    invalid = pattern.transition(pattern.initial_state, "select:roles", values=("role-0", "role-1"))
    valid = pattern.transition(invalid, "select:roles", values=("role-1",))

    assert invalid.staged == ("role-0", "role-1")
    assert invalid.committed == ()
    assert valid.staged == valid.committed == ("role-1",)


def test_immediate_modal_submission_commits_in_one_transition() -> None:
    pattern = sp.MultiChoicePanel(
        "Roles",
        (sp.MultiChoiceGroup("roles", "Roles", _options("role", 3)),),
        commit=sp.CommitPolicy.IMMEDIATE,
    )

    state = pattern.transition(pattern.initial_state, "modal", submitted={"selection": ("role-2",)})

    assert state.staged == state.committed == ("role-2",)
