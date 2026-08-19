"""Discord Actions adaptation, routing, and sticky presentation state."""

from collections.abc import Awaitable, Callable

from squid_layouts import ActionDisplay, PresentationSession, plan
from squid_layouts.actions import ActionEvent, ActionPolicy
from squid_layouts.discord import DISCORD_V2
from squid_layouts.presentation import StrategyState
from squid_layouts.scene import SceneButton, SceneRow, SceneSelect
from squid_layouts.semantic import Action, ActionGroup, Actions, Emphasis, Link


async def _act(event: ActionEvent) -> None: ...


def _actions(count: int, *, handler: Callable[[ActionEvent], Awaitable[None]] = _act) -> tuple[Action, ...]:
    return tuple(Action(f"action.{index}", f"Action {index + 1}", handler) for index in range(count))


def test_thirty_six_actions_fold_losslessly_into_twenty_five_and_eleven() -> None:
    result = plan(Actions(_actions(36), key="demo"), target=DISCORD_V2)

    selects = [node for node in result.scene.children if isinstance(node, SceneSelect)]
    assert [len(select.options) for select in selects] == [25, 11]
    assert {option.value for select in selects for option in select.options} == {
        f"action.{index}" for index in range(36)
    }
    assert {f"action.{index}" for index in range(36)} <= result.bindings.keys()
    assert result.report.events[0].code == "actions.grouped"
    assert result.metrics.states_explored == 1


def test_explicit_action_groups_never_merge() -> None:
    document = Actions(
        (
            ActionGroup("files", _actions(10)),
            ActionGroup("admin", tuple(Action(f"admin.{index}", f"Admin {index}", _act) for index in range(10))),
        ),
        key="toolbar",
        display=ActionDisplay.GROUPED,
    )

    result = plan(document, target=DISCORD_V2)
    selects = [node for node in result.scene.children if isinstance(node, SceneSelect)]

    assert [len(select.options) for select in selects] == [10, 10]
    assert [select.action for select in selects] == ["toolbar.files.0", "toolbar.admin.0"]


def test_strong_actions_and_links_remain_direct_unless_grouping_is_granted() -> None:
    strong = Action("delete", "Delete", _act, emphasis=Emphasis.STRONG)
    grouped = Action("archive", "Archive", _act)
    link = Link("docs", "Docs", "https://example.invalid")

    result = plan(
        Actions((strong, grouped, link), key="mixed", display=ActionDisplay.GROUPED),
        target=DISCORD_V2,
    )

    select = next(node for node in result.scene.children if isinstance(node, SceneSelect))
    rows = [node for node in result.scene.children if isinstance(node, SceneRow)]
    assert [option.value for option in select.options] == ["archive"]
    assert any(isinstance(item, SceneButton) and item.action == "delete" for row in rows for item in row.items)
    assert sum(len(row.items) for row in rows) == 2


def test_grouped_route_keeps_the_selected_actions_policy_and_handler() -> None:
    action = Action("read", "Read", _act, policy=ActionPolicy.PARALLEL_READ)
    result = plan(Actions((action,), key="demo", display=ActionDisplay.GROUPED), target=DISCORD_V2)
    select = next(node for node in result.scene.children if isinstance(node, SceneSelect))

    routed = result.bindings[select.action].routed(("read",))

    assert routed is not None
    assert routed.key == "read"
    assert routed.handler is _act
    assert routed.policy is ActionPolicy.PARALLEL_READ


def test_action_strategy_is_sticky_while_it_remains_valid() -> None:
    session = PresentationSession()
    first = plan(Actions(_actions(36), key="demo"), target=DISCORD_V2, session=session)
    second = plan(Actions(_actions(5), key="demo"), target=DISCORD_V2, session=session)

    assert first.report.events[0].code == "actions.grouped"
    assert second.report.events[0].code == "actions.grouped"


def test_adapter_version_change_resets_only_that_strategy() -> None:
    session = PresentationSession(strategies={"demo": StrategyState("demo", "discord.actions", 0, "individual")})

    result = plan(Actions(_actions(36), key="demo"), target=DISCORD_V2, session=session)

    assert result.report.events[0].code == "actions.grouped"
    assert session.strategies["demo"].adapter_version == 1


def test_more_than_seventy_five_actions_use_a_keyed_paged_picker() -> None:
    session = PresentationSession()
    document = Actions(_actions(76), key="demo")
    first = plan(document, target=DISCORD_V2, session=session)
    session.move_cursor("demo.default", 1)
    second = plan(document, target=DISCORD_V2, session=session)

    first_select = next(node for node in first.scene.children if isinstance(node, SceneSelect))
    second_select = next(node for node in second.scene.children if isinstance(node, SceneSelect))
    assert len(first_select.options) == 25
    assert first.scene.pagers[0].pages == 4
    assert first.scene.pagers[0].page == 0
    assert second.scene.pagers[0].page == 1
    assert second_select.options[0].value == "action.25"
