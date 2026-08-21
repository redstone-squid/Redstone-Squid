"""Author-sized glue budgets and heterogeneous region breaking."""

import pytest

import squid_layouts as sl
from squid_layouts.discord import DEFAULT_TARGET
from squid_layouts.planning import solve
from squid_layouts.planning.limits import V2Limits
from squid_layouts.primitives import Paginate, Text


class TestBudgetContract:
    def test_minimum_must_not_exceed_preferred_size(self) -> None:
        with pytest.raises(ValueError, match="min must not exceed prefer"):
            sl.budget(sl.paragraph("body"), min=101, prefer=100)

    @pytest.mark.parametrize("values", [(-1, 1, 0), (0, -1, 0), (0, 1, -1)])
    def test_sizes_must_not_be_negative(self, values: tuple[int, int, int]) -> None:
        minimum, preferred, stretch = values
        with pytest.raises(ValueError, match="must not be negative"):
            sl.budget(sl.paragraph("body"), min=minimum, prefer=preferred, stretch=stretch)

    def test_content_inside_the_stretch_band_stays_whole(self) -> None:
        body = "a" * 519 + "\n" + "b" * 520

        solved = solve(
            [
                sl.primitives.Budget(
                    (Text(body, overflow=Paginate(key="body")),),
                    minimum=500,
                    preferred=1000,
                    stretch=150,
                )
            ]
        )

        assert solved.pager is None
        assert solved.children[0].content == body  # type: ignore[union-attr]

    def test_over_band_pagination_balances_at_boundaries(self) -> None:
        body = "a" * 519 + "\n" + "b" * 520

        solved = solve(
            [
                sl.primitives.Budget(
                    (Text(body, overflow=Paginate(key="body")),),
                    minimum=400,
                    preferred=800,
                    stretch=100,
                )
            ]
        )

        assert solved.pager is not None
        assert [len(fragment) for fragment in solved.pager.fragments] == [519, 520]

    def test_collectively_unsatisfied_floors_raise(self) -> None:
        document = (
            sl.budget(sl.paragraph("a" * 100), min=80, prefer=100),
            sl.budget(sl.paragraph("b" * 100), min=80, prefer=100),
        )
        target = sl.planning.TargetProfile("small", 1, DEFAULT_TARGET.capabilities, V2Limits(total_text=100))

        with pytest.raises(sl.UnsolvableLayoutError, match="Budget floors need"):
            sl.plan(document, target=target)

    def test_best_effort_permits_its_floor_to_breach(self) -> None:
        document = (
            sl.budget(sl.best_effort(sl.paragraph("a" * 100)), min=80, prefer=100),
            sl.best_effort(sl.budget(sl.paragraph("b" * 100), min=80, prefer=100)),
        )
        target = sl.planning.TargetProfile("small", 1, DEFAULT_TARGET.capabilities, V2Limits(total_text=100))

        result = sl.plan(document, target=target)

        assert any("breached best-effort budget floor" in event.message for event in result.report.events)


class TestPaginateBreakPreferences:
    def test_values_are_validated(self) -> None:
        with pytest.raises(ValueError, match="min_fill"):
            Paginate(min_fill=-1)
        with pytest.raises(ValueError, match="widows"):
            Paginate(widows=0)
