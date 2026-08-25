"""Author-sized glue budgets and heterogeneous region breaking."""

import pytest

import squid_layouts as sl
from squid_discord import V2_TARGET
from squid_layouts.planning import measure
from squid_layouts.planning.limits import V2Limits
from squid_layouts.primitives import Paginate, Text
from squid_layouts.scene.model import ScenePanel, SceneText


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

        solved = measure(
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

        solved = measure(
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

    def test_over_band_pagination_can_snap_into_the_stretch_band(self) -> None:
        body = "a" * 1040 + "\n" + "b" * 160

        solved = measure(
            [
                sl.primitives.Budget(
                    (Text(body, overflow=Paginate(key="body")),),
                    minimum=500,
                    preferred=1000,
                    stretch=100,
                )
            ]
        )

        assert solved.pager is not None
        assert [len(fragment) for fragment in solved.pager.fragments] == [1040, 160]

    def test_collectively_unsatisfied_floors_raise(self) -> None:
        document = (
            sl.budget(sl.paragraph("a" * 100), min=80, prefer=100),
            sl.budget(sl.paragraph("b" * 100), min=80, prefer=100),
        )
        target = sl.planning.TargetProfile("small", 1, V2_TARGET.capabilities, V2Limits(total_text=100))

        with pytest.raises(sl.errors.UnsolvableLayoutError, match="Budget floors need"):
            sl.planning.plan(document, target=target)

    def test_best_effort_permits_its_floor_to_breach(self) -> None:
        document = (
            sl.budget(sl.best_effort(sl.paragraph("a" * 100)), min=80, prefer=100),
            sl.best_effort(sl.budget(sl.paragraph("b" * 100), min=80, prefer=100)),
        )
        target = sl.planning.TargetProfile("small", 1, V2_TARGET.capabilities, V2Limits(total_text=100))

        result = sl.planning.plan(document, target=target)

        assert any("breached best-effort budget floor" in event.message for event in result.report.events)


class TestPaginateBreakPreferences:
    def test_values_are_validated(self) -> None:
        with pytest.raises(ValueError, match="min_fill"):
            Paginate(min_fill=-1)
        with pytest.raises(ValueError, match="widows"):
            Paginate(widows=0)


class TestRegionPagination:
    @staticmethod
    def _texts(result: sl.scene.PlanResult) -> list[str]:
        panel = result.scene.components_v2.children[0]
        assert isinstance(panel, ScenePanel)
        return [child.content for child in panel.children if isinstance(child, SceneText)]

    def test_sugar_validates_its_region_contract(self) -> None:
        with pytest.raises(ValueError, match="key must not be empty"):
            sl.paged(sl.paragraph("body"), key="", chars=100)
        with pytest.raises(ValueError, match="chars must be positive"):
            sl.paged(sl.paragraph("body"), key="body", chars=0)
        with pytest.raises(ValueError, match="widows"):
            sl.paged(sl.paragraph("body"), key="body", chars=100, widows=0)

    def test_break_annotations_are_transparent_without_a_paged_region(self) -> None:
        result = sl.planning.plan(
            sl.group(sl.unbreakable(sl.paragraph("first")), sl.keep_with_next(sl.paragraph("second"))),
            target=V2_TARGET,
        )

        assert [child.content for child in result.scene.components_v2.children if isinstance(child, SceneText)] == [
            "first",
            "second",
        ]

    def test_a_section_pages_heterogeneous_children(self) -> None:
        document = sl.paged(
            sl.section(sl.heading("Report"), *(sl.paragraph(f"{index}: " + "x" * 30) for index in range(6))),
            key="report",
            chars=80,
        )

        first = sl.planning.plan(document, target=V2_TARGET)
        second = sl.planning.plan(document, target=V2_TARGET, positions={"report": sl.sources.Position(offset=1)})

        assert first.scene.pagers[0].pages == 3
        assert self._texts(first)[:3] == ["## Report", "0: " + "x" * 30, "1: " + "x" * 30]
        assert self._texts(second)[:2] == ["2: " + "x" * 30, "3: " + "x" * 30]
        assert first.scene.pagers[0].content_fingerprint == second.scene.pagers[0].content_fingerprint

    def test_keep_with_next_moves_a_heading_to_its_content(self) -> None:
        document = sl.paged(
            sl.block(
                sl.paragraph("a" * 45),
                sl.keep_with_next(sl.heading("Next")),
                sl.paragraph("b" * 35),
            ),
            key="chapters",
            chars=50,
        )

        first = sl.planning.plan(document, target=V2_TARGET)
        second = sl.planning.plan(document, target=V2_TARGET, positions={"chapters": sl.sources.Position(offset=1)})

        assert "## Next" not in self._texts(first)
        assert self._texts(second)[:2] == ["## Next", "b" * 35]

    def test_unbreakable_rejects_an_oversized_group(self) -> None:
        document = sl.paged(
            sl.block(sl.unbreakable(sl.group(sl.paragraph("a" * 30), sl.paragraph("b" * 30)))),
            key="atomic",
            chars=50,
        )

        with pytest.raises(sl.errors.UnsolvableLayoutError, match="unbreakable region child"):
            sl.planning.plan(document, target=V2_TARGET)

    def test_an_oversized_text_child_splits_losslessly(self) -> None:
        document = sl.paged(sl.block(sl.paragraph("x" * 120)), key="prose", chars=50)

        pages = [
            sl.planning.plan(document, target=V2_TARGET, positions={"prose": sl.sources.Position(offset=index)})
            for index in range(3)
        ]

        assert pages[0].scene.pagers[0].pages == 3
        content = "".join(self._texts(result)[0] for result in pages)
        assert content == "x" * 120

    def test_widows_keep_three_children_on_the_last_page(self) -> None:
        document = sl.paged(
            sl.block(*(sl.paragraph(str(index) * 20) for index in range(5))),
            key="widows",
            chars=65,
            widows=3,
        )

        last = sl.planning.plan(document, target=V2_TARGET, positions={"widows": sl.sources.Position(offset=1)})

        assert self._texts(last)[:3] == ["2" * 20, "3" * 20, "4" * 20]

    def test_a_large_region_is_broken_without_a_size_dependent_heuristic(self) -> None:
        document = sl.paged(
            sl.block(*(sl.paragraph("x" * 10) for _ in range(100))),
            key="large-region",
            chars=100,
        )

        first = sl.planning.plan(document, target=V2_TARGET)

        assert first.scene.pagers[0].pages == 10
        assert self._texts(first)[:10] == ["x" * 10] * 10
