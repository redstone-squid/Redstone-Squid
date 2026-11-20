"""Built-in pagination is contextual content evaluated before target certification."""

from collections.abc import Sequence
from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_reactivity import ReactiveWriteError
from squid_ui.authoring.semantic import Paragraph
from squid_ui.ext.discord import testing as discord_testing
from squid_ui.planning import plan
from squid_ui.planning.pagination.navigation import NavigationContext, NavNode, default_nav
from squid_ui.selectors import select


def test_builtin_footer_uses_each_candidate_view_without_outer_feedback() -> None:
    result = plan(
        sl.bullets(*(sl.bullet(str(i), key=str(i)) for i in range(5)), key="results", group_size=2),
        target=html.target(),
        inspect_layout=True,
    )
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("results"))
    assert region.navigation is not None
    assert region.navigation.index == 0 and region.navigation.extent == 3
    assert "Page 1 of 3" in html.Renderer().draw(result.scene)
    later = result.for_page({region.identity: 2})
    assert "Page 3 of 3" in html.Renderer().draw(later.scene)
    assert result.metrics.context_rounds == later.metrics.context_rounds == 0


def test_custom_footer_can_query_and_locate_the_current_candidate() -> None:
    observed: set[int] = set()

    def footer(ctx: sl.LayoutContext) -> str:
        region = ctx.region()
        selection = region.selection.require()
        assert selection is not None
        page = selection.number.require()
        observed.add(page)
        assert region.navigation is not None
        assert region.navigation.index == page - 1
        assert ctx.authored.query(select(Paragraph)).count() == 3
        visible = ctx.visible.query(select(Paragraph))
        assert visible.count() > 0
        locations = ctx.locate(visible.all()[0])
        assert locations.complete and any(fragment.visible for fragment in locations.fragments)
        return f"Position {page} ({visible.count()})"

    result = plan(
        sl.paged(
            sl.stack(*(sl.paragraph("x" * 30, key=f"item-{i}") for i in range(3))), key="body", chars=45, footer=footer
        ),
        target=html.target(),
        inspect_layout=True,
    )
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("body"))
    assert region.page_count == sl.Known(3)
    assert observed == {1, 2, 3}
    assert "Position 1 (1)" in sl.scene.Codec.dumps(result.scene)
    assert "Position 3 (1)" in sl.scene.Codec.dumps(result.for_page({region.identity: 2}).scene)


def test_builtin_chrome_applies_the_contextual_effect_guard() -> None:
    class Counter(sl.Component):
        value = sl.state(0)

        @override
        def render(self) -> sl.LayoutNode:
            return sl.paragraph(str(self.value))

    counter = Counter()

    def mutate(ctx: sl.LayoutContext) -> str:
        counter.value += 1
        return "changed"

    body = sl.stack(sl.paragraph("a" * 30), sl.paragraph("b" * 30))
    document = sl.paged(body, key="body", chars=45, footer=mutate)
    with pytest.raises(ReactiveWriteError):
        plan(document, target=html.target())
    assert counter.value == 0

    def sample(ctx: sl.LayoutContext) -> str:
        return str(counter.value)

    with pytest.raises(sl.errors.LayoutError, match="capture reactive inputs"):
        plan(sl.paged(body, key="body", chars=45, footer=sample), target=html.target())

    def invalid_nav(ctx: NavigationContext) -> Sequence[NavNode]:
        counter.value += 1
        return default_nav(ctx)

    with pytest.raises(ReactiveWriteError):
        plan(sl.bullets("a", "b", key="results", group_size=1), target=html.target(), nav=invalid_nav)
    assert counter.value == 0


def test_builtin_pagination_certifies_a_view_without_an_authored_context() -> None:
    result = plan(
        sl.bullets(
            sl.bullet("a" * 2100, key="first"),
            sl.bullet("b" * 5000, key="middle"),
            sl.bullet("c" * 2100, key="last"),
            key="results",
            group_size=1,
        ),
        target=discord_testing.target_profile("builtin-partial"),
    )
    assert result.certificate is sl.planning.CertificateScope.VIEW
    assert result.allocation is None
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("results"))
    assert region.group_count == sl.Known(3)
    assert isinstance(region.page_count, sl.Unknown)
    assert region.navigation is not None and region.navigation.extent is None
    assert region.navigation.on_seek is None
    assert all(pager.pages is None for pager in result.scene.pagers)
    assert "Page 1 of 3" not in sl.scene.Codec.dumps(result.scene)
    later = result.for_page({region.identity: 2})
    assert "c" * 2100 in sl.scene.Codec.dumps(later.scene)
    assert later.layout is not None
    selected = later.layout.region(sl.region_ref("results")).selection.require()
    assert selected is not None and isinstance(selected.number, sl.Unknown)
    with pytest.raises(sl.planning.JointInfeasibleError):
        result.for_page({region.identity: 1})
    assert "a" * 2100 in sl.scene.Codec.dumps(result.scene)


def test_custom_footer_handles_unknown_facts_in_the_same_context() -> None:
    def footer(ctx: sl.LayoutContext) -> str:
        return "More" if isinstance(ctx.region().page_count, sl.Unknown) else "Known"

    result = plan(
        sl.paged(
            sl.stack(sl.unbreakable(sl.paragraph("a" * 40)), sl.unbreakable(sl.paragraph("b" * 80))),
            key="body",
            chars=50,
            footer=footer,
        ),
        target=html.target(),
    )
    assert result.certificate is sl.planning.CertificateScope.VIEW
    assert "More" in html.Renderer().draw(result.scene)
    assert "Known" not in html.Renderer().draw(result.scene)
    assert all(pager.pages is None for pager in result.scene.pagers)
