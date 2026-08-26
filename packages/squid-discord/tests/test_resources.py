"""Named message-wide resource axes: the vocabulary the planner budgets in."""

import pytest

from squid_discord import DISCORD_V2_DPY27
from squid_discord import V2_LIMITS as LIMITS
from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning import measure, plan
from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.discord import components_v2_target
from squid_layouts.planning.layout_measurement.costing import measure_nodes
from squid_layouts.planning.layout_measurement.model import RText
from squid_layouts.planning.layout_measurement.text import BudgetRegion, make_unit, text_total
from squid_layouts.planning.limits import Axis, V2Limits
from squid_layouts.planning.target import ResourceCost
from squid_layouts.planning.types import DiscordAdapter
from squid_layouts.primitives import Never, Panel, Text, Variants


def _target(name: str, *, capabilities: frozenset[str] = frozenset(), limits: V2Limits = LIMITS):
    """A V2 target whose adapter supplies exactly `capabilities` and no extensions.

    Capabilities that are not Discord protocol facts belong to the adapter axis, which is
    what lets a test vary them without inventing a dialect.
    """
    return components_v2_target(AdapterProfile(DiscordAdapter, name, ">=1", capabilities=capabilities), limits=limits)


class TestResourceCost:
    def test_a_negative_cost_is_rejected_where_it_is_written(self) -> None:
        with pytest.raises(LayoutInvariantError, match="cannot cost -1"):
            ResourceCost({Axis.COMPONENTS: -1})

    def test_zero_axes_are_dropped_so_equal_spending_compares_equal(self) -> None:
        assert ResourceCost({Axis.COMPONENTS: 2}) == ResourceCost({Axis.COMPONENTS: 2, Axis.ATTACHMENTS: 0})

    def test_axes_are_stored_in_name_order_whatever_order_they_arrived_in(self) -> None:
        cost = ResourceCost({Axis.COMPONENTS: 1, Axis.ATTACHMENTS: 1, Axis.DISPLAY_TEXT: 1})

        assert cost.axes == (Axis.ATTACHMENTS, Axis.COMPONENTS, Axis.DISPLAY_TEXT)

    def test_addition_sums_every_axis_either_side_names(self) -> None:
        combined = ResourceCost({Axis.COMPONENTS: 2, Axis.DISPLAY_TEXT: 5}) + ResourceCost(
            {Axis.COMPONENTS: 3, Axis.ATTACHMENTS: 1}
        )

        assert combined.values == {Axis.ATTACHMENTS: 1, Axis.COMPONENTS: 5, Axis.DISPLAY_TEXT: 5}

    def test_overspending_names_every_offending_axis_not_just_the_first(self) -> None:
        """A document over two budgets should hear about both in one pass."""
        cost = ResourceCost({Axis.COMPONENTS: 41, Axis.DISPLAY_TEXT: 4001, Axis.ATTACHMENTS: 1})

        assert list(cost.over({Axis.COMPONENTS: 40, Axis.DISPLAY_TEXT: 4000, Axis.ATTACHMENTS: 10})) == [
            (Axis.COMPONENTS, 41, 40),
            (Axis.DISPLAY_TEXT, 4001, 4000),
        ]

    def test_an_unbudgeted_axis_is_unconstrained_rather_than_zero_capacity(self) -> None:
        assert ResourceCost({"embed_text": 9000}).within({Axis.COMPONENTS: 40})

    def test_cheaper_anywhere_is_per_axis_and_never_trades_one_for_another(self) -> None:
        components = ResourceCost({Axis.COMPONENTS: 1, Axis.DISPLAY_TEXT: 100})
        text = ResourceCost({Axis.COMPONENTS: 10, Axis.DISPLAY_TEXT: 5})

        # Neither dominates: one is blocked on components, the other on text, and the steps
        # that free each are different steps.
        assert components.cheaper_anywhere(text)
        assert text.cheaper_anywhere(components)

    def test_a_strictly_worse_cost_is_cheaper_nowhere(self) -> None:
        assert not ResourceCost({Axis.COMPONENTS: 10}).cheaper_anywhere(ResourceCost({Axis.COMPONENTS: 5}))


class TestTargetCapacities:
    def test_a_target_reads_its_budgets_from_its_limits(self) -> None:
        assert DISCORD_V2_DPY27.capacities == {
            Axis.DISPLAY_TEXT: LIMITS.total_text,
            Axis.COMPONENTS: LIMITS.total_components,
            Axis.ATTACHMENTS: LIMITS.attachments,
        }

    def test_a_reservation_shows_up_as_a_smaller_capacity(self) -> None:
        reserved = DISCORD_V2_DPY27.reserve(ResourceCost({Axis.COMPONENTS: 5}))

        assert reserved.capacity(Axis.COMPONENTS) == LIMITS.total_components - 5
        assert reserved.capacity(Axis.DISPLAY_TEXT) == LIMITS.total_text

    def test_an_axis_the_target_does_not_budget_has_no_capacity(self) -> None:
        assert DISCORD_V2_DPY27.capacity("embed_text") is None

    def test_reserving_an_unknown_axis_names_the_ones_that_exist(self) -> None:
        with pytest.raises(LayoutInvariantError, match="no reservable resource 'embed_text'"):
            DISCORD_V2_DPY27.reserve(ResourceCost({"embed_text": 1}))


class TestMeasuredCost:
    def test_a_measured_layout_reports_every_axis_it_spends(self) -> None:
        measured = measure([Panel(children=(Text("hello"), Text("world")))])

        assert measured.cost.get(Axis.COMPONENTS) == 3
        assert measured.cost.get(Axis.DISPLAY_TEXT) == len("hello") + len("world")

    def test_preferred_node_measurement_speaks_the_same_axes(self) -> None:
        cost = measure_nodes([Panel(children=(Text("hello"),))])

        assert cost.get(Axis.COMPONENTS) == 2
        assert text_total(cost) == 5

    def test_an_overspent_document_names_the_axis_in_its_failure(self) -> None:
        oversized = [Text(f"line {index}") for index in range(LIMITS.total_components + 1)]

        with pytest.raises(UnsolvableLayoutError, match=rf"41 {Axis.COMPONENTS} exceed target maximum 40"):
            plan(oversized, target=DISCORD_V2_DPY27)


class TestBudgetRegions:
    def test_a_budget_region_spanning_two_text_pools_is_rejected(self) -> None:
        """One `Budget` states one preferred size; applying it to two pools would double it."""
        units = [
            make_unit(Text("a"), RText(), 0, Axis.DISPLAY_TEXT),
            make_unit(Text("b"), RText(), 1, "embed_text"),
        ]
        region = BudgetRegion(tuple(unit for unit in units if unit is not None), 0, 10, 0, best_effort=False)

        with pytest.raises(LayoutInvariantError, match="spans the text axes display_text, embed_text"):
            _ = region.axis

    def test_a_single_pool_region_reports_that_pool(self) -> None:
        unit = make_unit(Text("a"), RText(), 0, Axis.DISPLAY_TEXT)
        assert unit is not None

        assert BudgetRegion((unit,), 0, 10, 0, best_effort=False).axis == Axis.DISPLAY_TEXT

    def test_a_region_holding_no_text_claims_no_pool(self) -> None:
        assert BudgetRegion((), 0, 10, 0, best_effort=False).axis is None


class TestParetoSearch:
    def test_a_candidate_cheaper_on_one_axis_is_not_pruned_by_one_cheaper_on_another(self) -> None:
        """Two documents blocked on different budgets need different steps to become legal."""
        components = ResourceCost({Axis.COMPONENTS: 39, Axis.DISPLAY_TEXT: 3999})
        text = ResourceCost({Axis.COMPONENTS: 5, Axis.DISPLAY_TEXT: 10})

        assert components.cheaper_anywhere(text) is False
        assert text.cheaper_anywhere(components) is True

    def test_the_search_still_finds_a_fit_when_ladders_trade_different_axes(self) -> None:
        """One ladder frees components, the other frees text, and neither dominates.

        A search pruning on a single scalar prices the two steps against each other and can
        walk the wrong one twice; both budgets have to be satisfied, so both must step.
        """
        wide = Variants.of(Panel(children=tuple(Text(f"w{index}") for index in range(20))), Text("w"))
        long = Variants.of(Text("x" * 3990, overflow=Never()), Text("x"))
        filler = [Text(f"f{index}") for index in range(19)]

        result = plan([wide, long, *filler], target=_target("test"))
        rendered = repr(result.scene.components_v2.children)

        assert "w0" not in rendered  # the component-heavy ladder gave way
        assert "x" * 3990 not in rendered  # and so did the text-heavy one
        assert "f18" in rendered  # while everything that fits is still shown
