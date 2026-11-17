"""Check the three use cases and preserve target and interaction context types."""

from typing import assert_type

from layout_api import Contextual, ElementRef, FragmentLocation, LayoutContext, contextual, element_ref, region_ref

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.authoring.semantic import Paragraph
from squid_ui.ext.discord.target_types import ClassicTarget, ComponentsV2Target
from squid_ui.selectors import Match, select
from squid_ui.target_types import RenderTarget

results = region_ref("results")
build = element_ref(Paragraph, "build-42")
rows = select(Paragraph).attr_starts_with("key", "build-")


def footer(ctx: LayoutContext) -> sl.LayoutNode:
    pages = ctx.region(results)
    selected = pages.selection
    if selected is None:
        return sl.paragraph(f"{pages.count} pages; results are hidden")
    assert_type(selected.number, int)
    return sl.paragraph(f"Page {selected.number} of {pages.count}")


def summary(ctx: LayoutContext) -> sl.LayoutNode:
    authored = ctx.authored.query(rows)
    visible = ctx.visible.query(rows)
    assert_type(authored.all(), tuple[Match[Paragraph], ...])
    return sl.paragraph(f"Showing {visible.count()} of {authored.count()} builds")


def cross_reference(ctx: LayoutContext) -> sl.LayoutNode:
    locations = ctx.locate(build, within=results)
    assert_type(locations, tuple[FragmentLocation, ...])
    pages = sorted({location.page.coordinates[-1].number for location in locations})
    if not pages:
        return sl.paragraph("This build has no placement in the results")
    return sl.paragraph("This build appears on pages " + ", ".join(map(str, pages)))


def native_footer(ctx: LayoutContext) -> sl.LayoutNode[ComponentsV2Target]:
    return sd.v2.panel(sd.v2.heading(f"{ctx.region(results).count} pages"))


def interactive(ctx: LayoutContext) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    async def press(event: sl.PressEvent[sd.Request]) -> None:
        await event.acknowledge()

    return sl.action_controls(sl.action_control("Open", press, key="open"), key="actions")


plain = contextual(footer, key="footer")
native = contextual(native_footer, key="native")
actions = contextual(interactive, key="interactive")
assert_type(plain, Contextual[RenderTarget, object])
assert_type(native, Contextual[ComponentsV2Target, object])
assert_type(actions, Contextual[ComponentsV2Target, sd.Request])
assert_type(build, ElementRef[Paragraph])
sl.Document[ComponentsV2Target]((plain, native))
sl.Document[ClassicTarget]((plain,))
sl.Document[ComponentsV2Target, sd.Request]((actions,))

# Negative probes: each should fail when uncommented.
# sl.Document[ClassicTarget]((native,))
# sl.Document[ComponentsV2Target, object]((actions,))
# footer(42)
# contextual(lambda ctx: 42, key="bad-output")
# LayoutContext().region(build)
# LayoutContext().locate(results)
