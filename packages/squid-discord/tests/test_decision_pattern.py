"""One-way decisions over component and router shells."""

import discord

import squid_layouts as sl
import squid_patterns as sp
from squid_discord import Everyone, Mount
from squid_discord.testing import commit_render, fake_interaction
from squid_layouts.semantic import Actions, Stack, Status


def _actions(rendered: sl.LayoutNode) -> tuple[sl.semantic.Action, ...]:
    assert isinstance(rendered, Stack)
    group = next(child for child in rendered.children if isinstance(child, Actions))
    return tuple(item for item in group.items if isinstance(item, sl.semantic.Action))


def _decision() -> sp.Decision:
    return sp.Decision(
        sl.paragraph("Delete this build?"),
        (
            sp.DecisionOption("delete", "Delete", sl.Tone.DANGER, sl.semantic.Emphasis.STRONG),
            sp.DecisionOption("keep", "Keep"),
        ),
        key="delete-build",
    )


def test_choose_sets_decided_once_and_later_transitions_are_noops() -> None:
    pattern = _decision()

    decided = pattern.transition(pattern.initial_state, "choose:delete")
    repeated = pattern.transition(decided, "choose:keep")

    assert decided == sp.DecisionState("delete")
    assert repeated is decided


def test_options_disable_and_status_appears_after_deciding() -> None:
    pattern = _decision()
    decided = pattern.transition(pattern.initial_state, "choose:delete")
    component = pattern.component()
    component.pattern_state = decided

    rendered = component.render()

    assert isinstance(rendered, Stack)
    assert all(not action.available for action in _actions(rendered))
    assert any(isinstance(child, Status) for child in rendered.children)


async def test_component_handler_receives_the_option_and_finish_action_ends_mount() -> None:
    seen: list[tuple[str, sp.DecisionState]] = []

    async def decided(event: sp.PatternEvent[sp.DecisionState], key: str) -> None:
        seen.append((key, event.state))

    component = _decision().component(on_decide=decided, finish_on={"delete"})
    mount = Mount(component, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("delete-build.delete", fake_interaction())

    assert component.pattern_state == sp.DecisionState("delete")
    assert seen == [("delete", sp.DecisionState("delete"))]
    assert mount._finished


async def test_confirm_wires_handlers_default_chrome_and_tone() -> None:
    seen: list[str] = []

    async def confirmed(event: sp.PatternEvent[sp.DecisionState]) -> None:
        seen.append(event.action)

    async def cancelled(event: sp.PatternEvent[sp.DecisionState]) -> None:
        seen.append(event.action)

    component = sp.confirm("Proceed?", on_confirm=confirmed, on_cancel=cancelled, tone=sl.Tone.DANGER)
    mount = Mount(component, access=Everyone(), timeout=None)
    view = commit_render(mount)
    buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    assert [button.label for button in buttons] == ["Confirm", "Cancel"]
    assert buttons[0].style == discord.ButtonStyle.danger

    await mount.dispatch("confirm.confirm", fake_interaction())

    assert seen == ["choose:confirm"]
    rendered = component.render()
    assert isinstance(rendered, Stack)
    assert all(not action.available for action in _actions(rendered))
    assert any(isinstance(child, Status) for child in rendered.children)


def test_router_shell_encodes_serializable_decision_state() -> None:
    pattern = _decision()
    routes: list[sp.PatternRoute[sp.DecisionState]] = []

    def route(request: sp.PatternRoute[sp.DecisionState]) -> str:
        routes.append(request)
        return f"decision:{request.state.decided}"

    sp.RouterShell(route).render(pattern, pattern.initial_state)

    assert sp.PatternRoute("choose:delete", sp.DecisionState("delete"), "next") in routes
