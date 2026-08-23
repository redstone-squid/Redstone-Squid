"""Named message-wide resource axes: the vocabulary the planner budgets in."""

import pytest

from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning import plan
from squid_layouts.discord import V2_LIMITS as LIMITS
from squid_layouts.discord import V2_TARGET
from squid_layouts.planning import TargetProfile, measure
from squid_layouts.planning.limits import ATTACHMENTS, COMPONENTS, DISPLAY_TEXT
from squid_layouts.planning.measurement import RText, _BudgetRegion, _make_unit, measure_nodes, text_total
from squid_layouts.planning.target import ResourceCost
from squid_layouts.primitives import Never, Panel, Text, Variants


class TestResourceCost:
    def test_a_negative_cost_is_rejected_where_it_is_written(self) -> None:
        with pytest.raises(LayoutInvariantError, match="cannot cost -1"):
            ResourceCost({COMPONENTS: -1})

    def test_zero_axes_are_dropped_so_equal_spending_compares_equal(self) -> None:
        assert ResourceCost({COMPONENTS: 2}) == ResourceCost({COMPONENTS: 2, ATTACHMENTS: 0})

    def test_axes_are_stored_in_name_order_whatever_order_they_arrived_in(self) -> None:
        cost = ResourceCost({COMPONENTS: 1, ATTACHMENTS: 1, DISPLAY_TEXT: 1})

        assert cost.axes == (ATTACHMENTS, COMPONENTS, DISPLAY_TEXT)

    def test_addition_sums_every_axis_either_side_names(self) -> None:
        combined = ResourceCost({COMPONENTS: 2, DISPLAY_TEXT: 5}) + ResourceCost({COMPONENTS: 3, ATTACHMENTS: 1})

        assert combined.values == {ATTACHMENTS: 1, COMPONENTS: 5, DISPLAY_TEXT: 5}

    def test_overspending_names_every_offending_axis_not_just_the_first(self) -> None:
        """A document over two budgets should hear about both in one pass."""
        cost = ResourceCost({COMPONENTS: 41, DISPLAY_TEXT: 4001, ATTACHMENTS: 1})

        assert list(cost.over({COMPONENTS: 40, DISPLAY_TEXT: 4000, ATTACHMENTS: 10})) == [
            (COMPONENTS, 41, 40),
            (DISPLAY_TEXT, 4001, 4000),
        ]

    def test_an_unbudgeted_axis_is_unconstrained_rather_than_zero_capacity(self) -> None:
        assert ResourceCost({"embed_text": 9000}).within({COMPONENTS: 40})

    def test_cheaper_anywhere_is_per_axis_and_never_trades_one_for_another(self) -> None:
        components = ResourceCost({COMPONENTS: 1, DISPLAY_TEXT: 100})
        text = ResourceCost({COMPONENTS: 10, DISPLAY_TEXT: 5})

        # Neither dominates: one is blocked on components, the other on text, and the steps
        # that free each are different steps.
        assert components.cheaper_anywhere(text)
        assert text.cheaper_anywhere(components)

    def test_a_strictly_worse_cost_is_cheaper_nowhere(self) -> None:
        assert not ResourceCost({COMPONENTS: 10}).cheaper_anywhere(ResourceCost({COMPONENTS: 5}))


class TestTargetCapacities:
    def test_a_target_reads_its_budgets_from_its_limits(self) -> None:
        assert V2_TARGET.capacities == {
            DISPLAY_TEXT: LIMITS.total_text,
            COMPONENTS: LIMITS.total_components,
            ATTACHMENTS: LIMITS.attachments,
        }

    def test_a_reservation_shows_up_as_a_smaller_capacity(self) -> None:
        reserved = V2_TARGET.reserve(ResourceCost({COMPONENTS: 5}))

        assert reserved.capacity(COMPONENTS) == LIMITS.total_components - 5
        assert reserved.capacity(DISPLAY_TEXT) == LIMITS.total_text

    def test_an_axis_the_target_does_not_budget_has_no_capacity(self) -> None:
        assert V2_TARGET.capacity("embed_text") is None

    def test_reserving_an_unknown_axis_names_the_ones_that_exist(self) -> None:
        with pytest.raises(LayoutInvariantError, match="no reservable resource 'embed_text'"):
            V2_TARGET.reserve(ResourceCost({"embed_text": 1}))


class TestMeasuredCost:
    def test_a_measured_layout_reports_every_axis_it_spends(self) -> None:
        measured = measure([Panel(children=(Text("hello"), Text("world")))])

        assert measured.cost.get(COMPONENTS) == 3
        assert measured.cost.get(DISPLAY_TEXT) == len("hello") + len("world")

    def test_preferred_node_measurement_speaks_the_same_axes(self) -> None:
        cost = measure_nodes([Panel(children=(Text("hello"),))])

        assert cost.get(COMPONENTS) == 2
        assert text_total(cost) == 5

    def test_an_overspent_document_names_the_axis_in_its_failure(self) -> None:
        oversized = [Text(f"line {index}") for index in range(LIMITS.total_components + 1)]

        with pytest.raises(UnsolvableLayoutError, match=rf"41 {COMPONENTS} exceed target maximum 40"):
            plan(oversized, target=V2_TARGET)


class TestBudgetRegions:
    def test_a_budget_region_spanning_two_text_pools_is_rejected(self) -> None:
        """One `Budget` states one preferred size; applying it to two pools would double it."""
        units = [
            _make_unit(Text("a"), RText(), 0, DISPLAY_TEXT),
            _make_unit(Text("b"), RText(), 1, "embed_text"),
        ]
        region = _BudgetRegion(tuple(unit for unit in units if unit is not None), 0, 10, 0, best_effort=False)

        with pytest.raises(LayoutInvariantError, match="spans the text axes display_text, embed_text"):
            _ = region.axis

    def test_a_single_pool_region_reports_that_pool(self) -> None:
        unit = _make_unit(Text("a"), RText(), 0, DISPLAY_TEXT)
        assert unit is not None

        assert _BudgetRegion((unit,), 0, 10, 0, best_effort=False).axis == DISPLAY_TEXT

    def test_a_region_holding_no_text_claims_no_pool(self) -> None:
        assert _BudgetRegion((), 0, 10, 0, best_effort=False).axis is None


class TestParetoSearch:
    def test_a_candidate_cheaper_on_one_axis_is_not_pruned_by_one_cheaper_on_another(self) -> None:
        """Two documents blocked on different budgets need different steps to become legal."""
        components = ResourceCost({COMPONENTS: 39, DISPLAY_TEXT: 3999})
        text = ResourceCost({COMPONENTS: 5, DISPLAY_TEXT: 10})

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

        result = plan([wide, long, *filler], target=TargetProfile("test", 1, limits=LIMITS))
        rendered = repr(result.scene.components_v2.children)

        assert "w0" not in rendered  # the component-heavy ladder gave way
        assert "x" * 3990 not in rendered  # and so did the text-heavy one
        assert "f18" in rendered  # while everything that fits is still shown
