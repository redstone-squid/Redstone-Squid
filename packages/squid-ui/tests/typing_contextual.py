"""Preserve target and interaction types through contextual callbacks and slots."""

from typing import assert_type

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.overflow import Paginate
from squid_ui.target_types import RenderTarget


def footer(ctx: sl.LayoutContext) -> sl.LayoutNode:
    count = ctx.region(sl.region_ref("results")).page_count
    match count:
        case sl.Known(value=n):
            assert_type(n, int)
            return sl.paragraph(f"{n} pages")
        case sl.Unknown():
            return sl.paragraph("More results")


def native(_ctx: sl.LayoutContext) -> sl.LayoutNode[ComponentsV2Target]:
    return sd.v2.heading("Native")


def interactive(_ctx: sl.LayoutContext) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    async def pressed(event: sl.PressEvent[sd.Request]) -> None:
        await event.acknowledge()

    return sl.action_controls(sl.action_control("Open", pressed, key="open"), key="actions")


plain = sl.context(footer, key="footer")
assert_type(plain, sl.Contextual[RenderTarget, object])
assert_type(sl.context(native, key="native"), sl.Contextual[ComponentsV2Target, object])
assert_type(sl.context(interactive, key="interactive"), sl.Contextual[ComponentsV2Target, sd.Request])
sl.Document[ComponentsV2Target]((plain, sl.context(native, key="native")))
sl.Document[ComponentsV2Target, sd.Request]((sl.context(interactive, key="interactive"),))


def pagination_footer(ctx: sl.LayoutContext) -> sl.TextLike:
    region = ctx.region()
    navigation = region.navigation
    if navigation is None:
        return "No results"
    assert_type(navigation.extent, int | None)
    assert_type(navigation.index, int | None)
    match region.page_count:
        case sl.Known(value=n):
            assert_type(n, int)
            return f"{n} pages"
        case sl.Unknown():
            return "More results"


sl.paged(sl.paragraph("Content"), key="pages", chars=100, footer=pagination_footer)
Paginate(key="native-pages", footer=pagination_footer)
