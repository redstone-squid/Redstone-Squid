"""Contextual content participates in fitting and stays private until delivery."""

from typing import override

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_reactivity import ReactiveWriteError
from squid_ui.authoring.semantic import Heading, ListItem
from squid_ui.planning import plan
from squid_ui.planning.contextual import ContextResolutionError
from squid_ui.runtime import ComponentRuntime
from squid_ui.selectors import select


def test_footer_resolves_materialized_count_and_generated_queries() -> None:
    results = sl.region_ref("results")
    calls: list[int] = []

    def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
        total = ctx.region(results).count.require()
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


def test_selection_dependent_footer_replans_and_reports_partial_facts() -> None:
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
    assert isinstance(region.count, sl.Unknown)
    assert region.groups == sl.Known(3)
    assert result.certificate is sl.planning.CertificateScope.VIEW
    assert result.scene.pagers and all(pager.pages is None for pager in result.scene.pagers)
    assert sl.scene.Codec.loads(sl.scene.Codec.dumps(result.scene)) == result.scene
    assert not result.layout.locate(sl.element_ref(ListItem, "item-21")).complete
    changed = result.for_page({region.identity: 2})
    assert "Page 3" in sl.scene.Codec.dumps(changed.scene)
    assert "Page 1" in sl.scene.Codec.dumps(result.scene)


def test_missing_required_region_and_feedback_cycles_have_distinct_failures() -> None:
    missing = sl.context(
        lambda ctx: sl.paragraph(str(ctx.region(sl.region_ref("missing")).count.require())), key="missing"
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
