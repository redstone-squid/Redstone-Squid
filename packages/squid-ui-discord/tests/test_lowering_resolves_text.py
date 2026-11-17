"""Planning resolves deferred text before scene serialization.

The fixture covers text-bearing primitive fields and nested records. Leaving a Text
unresolved would send it to downstream readers that expect resolved text.
"""

from dataclasses import replace
from typing import Any, cast

import pytest

from squid_i18n import tr
from squid_ui import scene
from squid_ui.ext.discord.planning import classic_target, components_v2_target
from squid_ui.ext.discord.primitives.nodes import (
    Card,
    CardAuthor,
    CardField,
    CardFooter,
    CardMedia,
    Code,
    Content,
    Footer,
    Gallery,
    GalleryItem,
    Heading,
    Lines,
    LinkButton,
    Option,
    RoutedButton,
    RoutedSelect,
    Section,
    SelectMenu,
    Text,
    Thumbnail,
)
from squid_ui.ext.discord.target_types import DiscordAdapter, DiscordTarget
from squid_ui.overflow import Paginate
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointUnsupportedError
from squid_ui.target_types import Renderable

ADAPTER = AdapterProfile(DiscordAdapter, "text-tests", "1")


async def _ignore_selection(_event: object) -> None:
    return None


def test_planning_leaves_no_deferred_text_behind() -> None:
    deferred = tr(t"test-hello = hello")
    authored = [
        Text(content=deferred),
        Heading(content=deferred),
        Footer(content=deferred),
        Code(content=deferred),
        Lines(lines=(deferred,)),
        RoutedButton(label=deferred, route_id="r"),
        RoutedSelect(options=(Option(label=deferred, value="v"),), route_id="r2"),
        Thumbnail(url="http://example.invalid/y.png", description=deferred),
        Content(content=deferred),
        LinkButton(label=deferred, url="https://example.invalid"),
        SelectMenu(
            options=(Option(label=deferred, value="v", description=deferred),),
            on_select=_ignore_selection,
            key="s",
            placeholder=deferred,
        ),
        Gallery((GalleryItem("https://example.invalid/g.png", deferred),)),
        Card(
            children=(Text(deferred),),
            title=deferred,
            fields=(CardField(deferred, deferred),),
            footer=CardFooter(deferred),
            author=CardAuthor(deferred),
            image=CardMedia("https://example.invalid/i.png", deferred),
        ),
    ]

    for node in authored:
        if isinstance(node, Thumbnail):
            node = Section((Text("Image"),), node)
        result = (
            plan(node, target=classic_target(ADAPTER))
            if isinstance(node, Card | Content)
            else plan(node, target=components_v2_target(ADAPTER))
        )
        # The scene codec refuses unresolved text expressions in any nested record.
        assert "hello" in scene.Codec.dumps(result.scene)


def test_unknown_renderables_are_rejected_with_their_path() -> None:
    class Unregistered(Renderable[DiscordTarget]):
        pass

    with pytest.raises(JointUnsupportedError) as error:
        plan(cast(Any, Unregistered()), target=components_v2_target(ADAPTER))
    assert "Unregistered" in str(error.value.diagnostics)


@pytest.mark.parametrize("make", [Text, Heading, Footer, Code, lambda content: Lines(lines=(content, content))])
def test_a_page_footer_is_resolved_on_every_text_primitive(make: Any) -> None:
    # Only Lines used to resolve its footer, so a paged traceback in a code block failed to plan.
    node = make(tr(t"test-hello = hello\n    world"))
    paged = replace(
        node, overflow=Paginate(key="pages", per=1, footer=lambda page, pages: tr(t"test-page = page {page}"))
    )

    result = plan(paged, target=components_v2_target(ADAPTER))
    assert "hello" in scene.Codec.dumps(result.scene)
    assert "page 1" in scene.Codec.dumps(result.scene)
    assert result.allocation is not None
    occurrence = next(iter(result.allocation.regions))
    second = result.for_page({occurrence: 1})
    assert "page 2" in scene.Codec.dumps(second.scene)
