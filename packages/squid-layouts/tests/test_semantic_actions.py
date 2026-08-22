"""Discord Actions adaptation, routing, and sticky presentation state."""

from collections.abc import Awaitable, Callable

import pytest

from squid_layouts import (
    ActionDisplay,
    Asset,
    Download,
    InlineAsset,
    List,
    ListItem,
    Paragraph,
    Position,
    fallback,
    plan,
    truncate,
)
from squid_layouts.actions import ActionEvent, ActionPolicy
from squid_layouts.discord import V2_TARGET
from squid_layouts.primitives import Lines, Paginate, Panel, Sep, Text, Variant, Variants, alts
from squid_layouts.runtime import PresentationSession, apply_updates
from squid_layouts.runtime.presentation import StrategyState
from squid_layouts.scene.model import SceneButton, ScenePanel, SceneRow, SceneSelect, SceneText
from squid_layouts.semantic import Action, ActionGroup, Actions, Emphasis, Flexibility, Link, Stack


async def _act(event: ActionEvent) -> None: ...


def _actions(count: int, *, handler: Callable[[ActionEvent], Awaitable[None]] = _act) -> tuple[Action, ...]:
    return tuple(Action(f"action.{index}", f"Action {index + 1}", handler) for index in range(count))


def test_thirty_six_actions_fold_losslessly_into_twenty_five_and_eleven() -> None:
    result = plan(Actions(_actions(36), key="demo"), target=V2_TARGET)

    selects = [node for node in result.scene.components_v2.children if isinstance(node, SceneSelect)]
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

    result = plan(document, target=V2_TARGET)
    selects = [node for node in result.scene.components_v2.children if isinstance(node, SceneSelect)]

    assert [len(select.options) for select in selects] == [10, 10]
    assert [select.action for select in selects] == ["toolbar.files.0", "toolbar.admin.0"]


def test_strong_actions_and_links_remain_direct_unless_grouping_is_granted() -> None:
    strong = Action("delete", "Delete", _act, emphasis=Emphasis.STRONG)
    grouped = Action("archive", "Archive", _act)
    link = Link("docs", "Docs", "https://example.invalid")

    result = plan(
        Actions((strong, grouped, link), key="mixed", display=ActionDisplay.GROUPED),
        target=V2_TARGET,
    )

    select = next(node for node in result.scene.components_v2.children if isinstance(node, SceneSelect))
    rows = [node for node in result.scene.components_v2.children if isinstance(node, SceneRow)]
    assert [option.value for option in select.options] == ["archive"]
    assert any(isinstance(item, SceneButton) and item.action == "delete" for row in rows for item in row.items)
    assert sum(len(row.items) for row in rows) == 2


def test_grouped_route_keeps_the_selected_actions_policy_and_handler() -> None:
    action = Action("read", "Read", _act, policy=ActionPolicy.PARALLEL_READ)
    result = plan(Actions((action,), key="demo", display=ActionDisplay.GROUPED), target=V2_TARGET)
    select = next(node for node in result.scene.components_v2.children if isinstance(node, SceneSelect))

    routed = result.bindings[select.action].routed(("read",))

    assert routed is not None
    assert routed.key == "read"
    assert routed.handler is _act
    assert routed.policy is ActionPolicy.PARALLEL_READ


def test_actions_choose_a_global_fit_instead_of_root_pagination() -> None:
    dense_document = (
        *(Paragraph(f"component {index}") for index in range(35)),
        Actions(_actions(5), key="demo"),
    )

    dense = plan(dense_document, target=V2_TARGET)
    roomy = plan(Actions(_actions(5), key="demo"), target=V2_TARGET)

    assert dense.report.events[0].code == "actions.grouped"
    assert dense.metrics.states_explored == 2
    assert not dense.scene.pagers
    assert sum(isinstance(node, SceneSelect) for node in dense.scene.components_v2.children) == 1
    assert roomy.report.events[0].code == "actions.individual"
    assert roomy.metrics.states_explored == 1
    assert sum(len(node.items) for node in roomy.scene.components_v2.children if isinstance(node, SceneRow)) == 5


def test_actions_find_a_global_fit_alongside_a_local_pager() -> None:
    document = (
        *(Paragraph(f"component {index}") for index in range(33)),
        Lines(("first", "second"), overflow=Paginate(key="local", per=1)),
        Actions(_actions(5), key="demo"),
    )

    result = plan(document, target=V2_TARGET)

    assert result.report.events[0].code == "actions.grouped"
    assert result.metrics.states_explored == 2
    assert [(pager.key, pager.pages) for pager in result.scene.pagers] == [("local", 2)]
    assert not result.metrics.search_fallback


def test_inactive_fallback_axes_do_not_spend_the_global_search_budget() -> None:
    session = PresentationSession(strategies={"visible": StrategyState("visible", "discord.actions", 1, "individual")})
    inactive = Stack(tuple(Actions(_actions(1), key=f"inactive-{index}") for index in range(9)))
    document = (
        *(Paragraph(f"component {index}") for index in range(35)),
        fallback(Paragraph("primary"), inactive),
        Actions(_actions(5), key="visible", flexibility=Flexibility.STABLE),
    )

    result = plan(document, target=V2_TARGET, session=session)

    assert any(event.code == "actions.grouped" and event.path == "$.36" for event in result.report.events)
    assert result.metrics.states_explored == 2
    assert not result.metrics.search_fallback


def test_fallback_axes_are_discovered_when_their_rung_becomes_reachable() -> None:
    document = (
        *(Paragraph(f"component {index}") for index in range(35)),
        fallback(
            Stack(tuple(Paragraph(f"primary {index}") for index in range(10))),
            Actions(_actions(5), key="fallback-actions"),
        ),
    )

    result = plan(document, target=V2_TARGET)

    assert any(event.code == "actions.grouped" and event.path == "$.35.alternate.0" for event in result.report.events)
    assert any(event.code == "layout.degradation.semantic_fallback" for event in result.report.events)
    assert sum(isinstance(node, SceneSelect) for node in result.scene.components_v2.children) == 1
    assert not result.metrics.search_fallback


def test_degraded_global_fit_prefers_less_loss_before_display_preference() -> None:
    structural_fallback = Variants.of(
        Panel(tuple(Sep() for _ in range(35))),
        Text("compact details"),
    )
    document = (
        Text("x" * 5000, overflow=alts("summary")),
        structural_fallback,
        Actions(_actions(5), key="demo"),
    )

    result = plan(document, target=V2_TARGET)

    assert result.report.events[0].code == "actions.grouped"
    assert "compact details" not in {
        node.content for node in result.scene.components_v2.children if hasattr(node, "content")
    }
    assert result.metrics.states_explored == 4


def test_action_strategy_is_sticky_while_it_remains_valid() -> None:
    session = PresentationSession()
    first = plan(Actions(_actions(36), key="demo"), target=V2_TARGET, session=session)
    apply_updates(session, first.session_updates)
    second = plan(Actions(_actions(5), key="demo"), target=V2_TARGET, session=session)

    assert first.report.events[0].code == "actions.grouped"
    assert second.report.events[0].code == "actions.grouped"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(5, "actions.individual"), (36, "actions.grouped"), (76, "actions.paged")],
)
def test_fresh_action_strategy_matrix(count: int, expected: str) -> None:
    result = plan(Actions(_actions(count), key="demo"), target=V2_TARGET, session=PresentationSession())

    assert result.report.events[0].code == expected


@pytest.mark.parametrize(
    ("initial", "changed", "expected"),
    [
        (5, 5, "actions.individual"),
        (5, 36, "actions.grouped"),
        (36, 5, "actions.grouped"),
        (36, 76, "actions.grouped"),
        (76, 36, "actions.grouped"),
    ],
)
def test_sticky_action_strategy_grow_and_shrink_matrix(initial: int, changed: int, expected: str) -> None:
    session = PresentationSession()
    first = plan(Actions(_actions(initial), key="demo"), target=V2_TARGET, session=session)
    apply_updates(session, first.session_updates)

    result = plan(Actions(_actions(changed), key="demo"), target=V2_TARGET, session=session)

    assert result.report.events[0].code == expected


def test_reordering_actions_does_not_change_a_sticky_strategy() -> None:
    session = PresentationSession()
    actions = _actions(36)
    first = plan(Actions(actions, key="demo"), target=V2_TARGET, session=session)
    apply_updates(session, first.session_updates)

    result = plan(Actions(tuple(reversed(actions)), key="demo"), target=V2_TARGET, session=session)

    assert result.report.events[0].code == "actions.grouped"


def test_adapter_version_change_resets_only_that_strategy() -> None:
    session = PresentationSession(strategies={"demo": StrategyState("demo", "discord.actions", 0, "individual")})

    result = plan(Actions(_actions(36), key="demo"), target=V2_TARGET, session=session)
    apply_updates(session, result.session_updates)

    assert result.report.events[0].code == "actions.grouped"
    assert session.strategies["demo"].adapter_version == 1


def test_more_than_seventy_five_actions_use_a_keyed_paged_picker() -> None:
    session = PresentationSession()
    document = Actions(_actions(76), key="demo")
    first = plan(document, target=V2_TARGET, session=session)
    apply_updates(session, first.session_updates)
    session.move_cursor("demo.default", Position(offset=1))
    second = plan(document, target=V2_TARGET, session=session)

    first_select = next(node for node in first.scene.components_v2.children if isinstance(node, SceneSelect))
    second_select = next(node for node in second.scene.components_v2.children if isinstance(node, SceneSelect))
    assert len(first_select.options) == 25
    assert first.scene.pagers[0].pages == 4
    assert first.scene.pagers[0].page == 0
    assert second.scene.pagers[0].page == 1
    assert second_select.options[0].value == "action.25"


def test_search_budget_exhaustion_reports_the_best_incumbent() -> None:
    # The incumbent fits but loses text, so a cheaper representation could still have won.
    # The budget stops the search before it can rule one out, which is what it reports.
    document = (truncate(Paragraph("x" * 5000)), Actions(_actions(5), key="demo"))

    result = plan(document, target=V2_TARGET, search_budget=1)

    assert result.metrics.search_fallback
    assert result.metrics.states_explored == 1
    assert result.report.events[0].code == "planner.search_fallback"
    assert result.report.events[0].severity.value == "warning"
    assert len(result.bindings) == 5


def test_a_provably_optimal_first_candidate_reports_no_fallback() -> None:
    """A budget of one is not a truncated search when nothing left could have beaten it."""
    result = plan(Actions(_actions(5), key="demo"), target=V2_TARGET, search_budget=1)

    assert result.metrics.states_explored == 1
    assert not result.metrics.search_fallback
    assert [event.code for event in result.report.events] == ["actions.individual"]


def _texts(result) -> set[str]:
    """Every string the scene will display, panels included."""
    found: set[str] = set()

    def walk(children) -> None:
        for child in children:
            if isinstance(child, SceneText):
                found.add(child.content)
            elif isinstance(child, ScenePanel):
                walk(child.children)

    walk(result.scene.components_v2.children)
    return found


def test_an_unopened_branch_leaves_no_trace_of_itself() -> None:
    """A hidden branch is not lowered at all, so it cannot stage anything on the way past."""
    hidden = Stack(
        (
            Actions(_actions(3), key="hidden-actions"),
            List(tuple(ListItem(str(index), f"hidden {index}") for index in range(40)), key="hidden-list"),
            Download(
                "hidden-download",
                "Hidden",
                Asset("hidden", "hidden.txt", "text/plain", InlineAsset(b"x")),
            ),
        )
    )
    session = PresentationSession()

    result = plan(fallback(Paragraph("visible"), hidden), target=V2_TARGET, session=session)

    assert _texts(result) == {"visible"}
    assert not result.scene.pagers
    assert not result.scene.assets
    assert not result.bindings
    assert not any("hidden" in event.path for event in result.report.events)
    assert all("hidden" not in getattr(update, "key", "") for update in result.session_updates)


def test_opening_a_fallback_abandons_the_decisions_under_the_old_branch() -> None:
    """Two spellings of one candidate would cost an extra evaluation; there is only one."""
    inner = fallback(
        Stack(tuple(Paragraph(f"inner primary {index}") for index in range(8))),
        Actions(_actions(5), key="inner-actions"),
    )
    document = (
        *(Paragraph(f"component {index}") for index in range(33)),
        fallback(
            Stack(
                (
                    Actions(_actions(5), key="outer-actions"),
                    *(Paragraph(f"outer {index}") for index in range(10)),
                )
            ),
            inner,
        ),
    )

    result = plan(document, target=V2_TARGET)
    rendered = _texts(result)

    # individual, grouped, the opened branch, then the nested branch: four, not five. The
    # outer picker's strategy does not survive its branch closing to spell a fifth.
    assert result.metrics.states_explored == 4
    assert not any(event.path.startswith("$.33.primary") for event in result.report.events)
    assert any(
        event.code == "actions.individual" and event.path == "$.33.alternate.0.alternate.0"
        for event in result.report.events
    )
    assert "outer 0" not in rendered
    assert "inner primary 0" not in rendered
    assert len([event for event in result.report.events if event.code.endswith("semantic_fallback")]) == 2


def test_a_strategy_and_a_ladder_rung_are_weighed_against_each_other() -> None:
    """One frontier: a lossless representation change beats a ladder step that costs loss."""
    document = (
        *(Paragraph(f"component {index}") for index in range(34)),
        Variants.of(Panel((Text("rich"), Text("detail"))), Text("compact")),
        Actions(_actions(5), key="demo"),
    )

    result = plan(document, target=V2_TARGET)
    rendered = _texts(result)

    assert "compact" not in rendered  # the ladder kept its preferred rung
    assert any(event.code == "actions.grouped" for event in result.report.events)
    assert not any(event.code.startswith("layout.degradation") for event in result.report.events)


def test_capability_filtering_keeps_rung_selection_stable() -> None:
    """Unsupported rungs are removed before search, so the survivors number from zero."""
    ladder = Variants(
        (
            Variant((Text("needs an extension"),), requires=frozenset({"target.absent"})),
            Variant((Panel((Text("rich"), Text("detail"))),)),
            Variant((Text("compact"),)),
        )
    )
    document = (*(Paragraph(f"component {index}") for index in range(34)), ladder)

    result = plan(document, target=V2_TARGET)
    rendered = _texts(result)
    panels = [node for node in result.scene.components_v2.children if isinstance(node, ScenePanel)]

    assert {"rich", "detail"} <= rendered
    assert "compact" not in rendered
    assert len(panels) == 1
