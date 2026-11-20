"""Contextual content participates in fitting and stays private until delivery."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_reactivity import ReactiveWriteError
from squid_ui.authoring.semantic import Heading, ListItem
from squid_ui.ext.discord import testing as discord_testing
from squid_ui.planning import plan
from squid_ui.planning.contextual import ContextResolutionError
from squid_ui.runtime import ComponentRuntime
from squid_ui.selectors import select


def test_footer_resolves_materialized_count_and_generated_queries() -> None:
    results = sl.region_ref("results")
    calls: list[int] = []

    def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
        total = ctx.region(results).page_count.require()
        calls.append(total)
        assert ctx.authored.query(select(Heading)).count() == 0
        return sl.heading(f"{total} pages")

    document = sl.stack(
        sl.bullets(*(sl.bullet(str(i), key=f"item-{i}") for i in range(23)), key="results", group_size=10),
        sl.context(footer, key="footer"),
    )
    result = plan(document, target=html.target())
    assert "3 pages" in sl.scene.Codec.dumps(result.scene)
    assert result.metrics.context_rounds >= 2
    assert result.certificate is sl.planning.CertificateScope.FAMILY
    assert result.layout is not None
    assert result.layout.placed.query(select(Heading)).count() == 1
    assert result.layout.authored.query(select(Heading)).count() == 0
    assert calls == [3, 3]


def test_selection_dependent_footer_replans_with_snapshot_scoped_totals() -> None:
    results = sl.region_ref("results")

    def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
        selection = ctx.region(results).selection.require()
        assert selection is not None
        return sl.paragraph(f"Page {selection.number.require()}")

    result = plan(
        sl.stack(
            sl.bullets(*(sl.bullet(str(i), key=f"item-{i}") for i in range(23)), key="results", group_size=10),
            sl.context(footer, key="footer"),
        ),
        target=html.target(),
    )
    assert "Page 1" in sl.scene.Codec.dumps(result.scene)
    assert result.layout is not None
    region = result.layout.region(results)
    assert region.page_count == sl.Known(3)
    assert region.group_count == sl.Known(3)
    assert result.certificate is sl.planning.CertificateScope.VIEW
    assert result.scene.pagers and all(pager.pages == 3 for pager in result.scene.pagers)
    assert sl.scene.Codec.loads(sl.scene.Codec.dumps(result.scene)) == result.scene
    assert result.layout.locate(sl.element_ref(ListItem, "item-21")).complete
    changed = result.for_page({region.identity: 2})
    assert "Page 3" in sl.scene.Codec.dumps(changed.scene)
    assert "Page 1" in sl.scene.Codec.dumps(result.scene)


def test_missing_required_region_and_feedback_cycles_have_distinct_failures() -> None:
    missing = sl.context(
        lambda ctx: sl.paragraph(str(ctx.region(sl.region_ref("missing")).page_count.require())), key="missing"
    )
    with pytest.raises(ContextResolutionError, match="unresolved required facts"):
        plan(missing, target=html.target())

    def oscillate(ctx: sl.LayoutContext) -> sl.LayoutNode:
        return sl.heading("present") if ctx.placed.query(select(Heading)).count() == 0 else sl.group()

    with pytest.raises(ContextResolutionError, match="oscillates"):
        plan(sl.context(oscillate, key="switch"), target=html.target())


def test_context_callbacks_cannot_write_or_sample_live_reactive_state() -> None:
    class Counter(sl.Component):
        value = sl.state(0)

        @override
        def render(self) -> sl.LayoutNode:
            return sl.paragraph(str(self.value))

    counter = Counter()

    def write(_ctx: sl.LayoutContext) -> sl.LayoutNode:
        counter.value += 1
        return sl.paragraph("changed")

    with pytest.raises(ReactiveWriteError):
        plan(sl.context(write, key="write"), target=html.target())
    assert counter.value == 0
    with pytest.raises(sl.errors.LayoutError, match="capture reactive inputs"):
        plan(sl.context(lambda _ctx: sl.paragraph(str(counter.value)), key="read"), target=html.target())
    with pytest.raises(sl.errors.LayoutError, match="constructed during rendering"):
        plan(sl.context(lambda _ctx: Counter(), key="construct"), target=html.target())


async def test_declared_slots_prepare_and_remain_logically_mounted_when_omitted() -> None:
    events: list[str] = []

    class Editor(sl.Component):
        @override
        async def on_load(self) -> None:
            events.append("load")

        @override
        def on_mount(self) -> None:
            events.append("mount")

        @override
        def render(self) -> sl.LayoutNode:
            return sl.paragraph("editor")

    class Screen(sl.Component):
        shown = sl.state(False)

        def __init__(self) -> None:
            self.editor = Editor()

        @override
        def render(self) -> sl.LayoutNode:
            shown = self.shown
            editor = self.editor.keyed("editor")
            return sl.context(lambda _ctx: editor if shown else (), key="conditional", components=(editor,))

    screen = Screen()
    runtime = ComponentRuntime(screen)
    discovered = runtime.render(defer=lambda component: component is screen.editor and not component._loaded)
    assert discovered.deferred_paths == {"editor": screen.editor}
    await runtime.preparation.prepare(discovered.deferred_paths)
    tree = runtime.render()
    result = plan(tree.nodes, target=html.target())
    assert "editor" not in sl.scene.Codec.dumps(result.scene)
    assert events == ["load"]
    runtime.commit(tree)
    assert events == ["load", "mount"]
    screen.shown = True
    tree = runtime.render(reuse_committed=True)
    result = plan(tree.nodes, target=html.target())
    assert "editor" in sl.scene.Codec.dumps(result.scene)
    runtime.commit(tree)
    assert events == ["load", "mount"]
    runtime.finish()


def test_nonconverging_branch_uses_a_declared_fallback() -> None:
    def oscillate(ctx: sl.LayoutContext) -> sl.LayoutNode:
        return sl.heading("present") if ctx.placed.query(select(Heading)).count() == 0 else sl.group()

    result = plan(sl.fallback(sl.context(oscillate, key="switch"), sl.paragraph("safe")), target=html.target())
    assert "safe" in sl.scene.Codec.dumps(result.scene)
    assert any(event.code == "context.rejected" for event in result.report.events)


def test_authored_matches_resolve_without_claiming_generated_content_is_authored() -> None:
    def summary(ctx: sl.LayoutContext) -> sl.LayoutNode:
        item = ctx.authored.query(select(ListItem).key("item")).one()
        assert ctx.locate(item).complete
        assert ctx.locate(item).fragments
        return sl.paragraph("found")

    result = plan(
        sl.stack(sl.bullets(sl.bullet("one", key="item"), key="results"), sl.context(summary, key="summary")),
        target=html.target(),
    )
    assert "found" in sl.scene.Codec.dumps(result.scene)


def test_context_references_are_scoped_to_the_producing_component_placement() -> None:
    class Panel(sl.Component):
        def __init__(self, count: int) -> None:
            self.count = count

        @override
        def render(self) -> sl.LayoutNode:
            def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
                groups = ctx.region(sl.region_ref("results")).group_count.require()
                located = ctx.locate(sl.element_ref(ListItem, "item-0"))
                assert len(located.fragments) == 1
                return sl.paragraph(f"{groups} groups")

            return sl.stack(
                sl.bullets(
                    *(sl.bullet(str(i), key=f"item-{i}") for i in range(self.count)), key="results", group_size=2
                ),
                sl.context(footer, key="footer"),
            )

    class Root(sl.Component):
        def __init__(self) -> None:
            self.left, self.right = Panel(3), Panel(7)

        @override
        def render(self) -> sl.LayoutNode:
            return sl.stack(self.left.keyed("left"), self.right.keyed("right"))

    runtime = ComponentRuntime(Root())
    tree = runtime.render()
    result = plan(tree.nodes, target=html.target())
    text = sl.scene.Codec.dumps(result.scene)
    assert "2 groups" in text and "4 groups" in text
    runtime.discard()
    runtime.finish()


def test_an_unfittable_unselected_fragment_does_not_block_the_current_view() -> None:
    document = sl.stack(
        sl.paged(
            sl.stack(
                sl.unbreakable(sl.paragraph("a" * 40)),
                sl.unbreakable(sl.paragraph("b" * 80)),
            ),
            key="body",
            chars=50,
        ),
        sl.context(lambda _ctx: sl.paragraph("ready"), key="status"),
    )
    result = plan(document, target=html.target())
    assert "a" * 40 in sl.scene.Codec.dumps(result.scene)
    assert "b" * 80 not in sl.scene.Codec.dumps(result.scene)
    assert result.certificate is sl.planning.CertificateScope.VIEW
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("body"))
    assert isinstance(region.page_count, sl.Unknown)
    assert result.report.search is not None and not result.report.search.optimal
    with pytest.raises(sl.planning.JointInfeasibleError):
        result.for_page({region.identity: 1})
    assert "a" * 40 in sl.scene.Codec.dumps(result.scene)


def test_an_uncertified_prefix_leaves_the_later_rendered_ordinal_unknown() -> None:
    result = plan(
        sl.stack(
            sl.bullets(
                sl.bullet("a" * 2100, key="first"),
                sl.bullet("b" * 5000, key="middle"),
                sl.bullet("c" * 2100, key="last"),
                key="results",
                group_size=1,
            ),
            sl.context(lambda _ctx: sl.paragraph("ready"), key="status"),
        ),
        target=discord_testing.target_profile("partial-prefix"),
    )
    assert result.layout is not None
    region = result.layout.region(sl.region_ref("results"))
    later = result.for_page({region.identity: 2})
    assert "c" * 2100 in sl.scene.Codec.dumps(later.scene)
    assert later.layout is not None
    selected = later.layout.region(sl.region_ref("results")).selection.require()
    assert selected is not None and isinstance(selected.number, sl.Unknown)
    locations = later.layout.locate(sl.element_ref(ListItem, "last"))
    assert locations.fragments and not locations.complete
    assert isinstance(locations.fragments[0].page.coordinates[-1].number, sl.Unknown)


def test_contextual_content_in_an_equivalent_representation_is_evaluated() -> None:
    def content(_ctx: sl.LayoutContext) -> sl.LayoutNode:
        return sl.paragraph("generated")

    document = sl.alternatives(
        sl.alternative(sl.context(content, key="body"), key="plain", fidelity=sl.equivalent()),
        content=sl.context(content, key="body"),
        key="representations",
    )
    result = plan(document, target=html.target())
    assert "generated" in sl.scene.Codec.dumps(result.scene)


def test_generated_footer_changes_real_page_allocation_before_certification() -> None:
    observed: list[int] = []

    def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
        count = ctx.region(sl.region_ref("items")).page_count.require()
        observed.append(count)
        return sl.paragraph("f" * (10 * count))

    document = sl.budget(
        sl.region(
            sl.bullets(*(sl.bullet("x" * 30, key=f"item-{i}") for i in range(4)), key="items", page_size=2),
            key="body",
            title="",
            footer=(sl.context(footer, key="footer"),),
        ),
        min=0,
        prefer=75,
    )
    result = plan(document, target=html.target())
    assert result.layout is not None
    count = result.layout.region(sl.region_ref("items")).page_count.require()
    assert len(set(observed)) > 1
    assert observed[-1] == count
    assert "f" * (10 * count) in sl.scene.Codec.dumps(result.scene)
    assert result.certificate is sl.planning.CertificateScope.FAMILY
