"""Lowering resolves deferred text before measurement and target conversion.

The fixture covers text-bearing primitive fields and nested records. Leaving a Message
unresolved would send it to downstream readers that expect resolved text.
"""

from dataclasses import replace
from typing import Any, cast

import pytest

from squid_i18n import tr
from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.limits import LIMITS
from squid_ui.planning.semantic_adaptation.lowering import lower_semantics
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
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
    SelectMenu,
    Text,
    Thumbnail,
)
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.target_types import DiscordTarget, Renderable
from squid_ui.text import NEUTRAL, Message

_TEXT_FIELDS = ("content", "label", "description", "placeholder")


async def _ignore_selection(_event: object) -> None:
    return None


def _deferred_text_in(node: object) -> list[str]:
    name = type(node).__name__
    found = [f"{name}.{field}" for field in _TEXT_FIELDS if isinstance(getattr(node, field, None), Message)]
    found += [
        f"{name}.options.label" for option in getattr(node, "options", ()) or () if isinstance(option.label, Message)
    ]
    found += [f"{name}.lines[]" for line in getattr(node, "lines", ()) or () if isinstance(line, Message)]
    for field in ("children", "texts"):
        found += [name for child in getattr(node, field, ()) or () for name in _deferred_text_in(child)]
    for field in ("image", "thumbnail", "footer", "author"):
        if (child := getattr(node, field, None)) is not None:
            found += _deferred_text_in(child)
    for field in getattr(node, "fields", ()) or ():
        found += _deferred_text_in(field)
    for value in ("name", "value", "text"):
        if isinstance(getattr(node, value, None), Message):
            found.append(f"{name}.{value}")
    return found


def test_lowering_leaves_no_deferred_text_behind() -> None:
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

    lowered = lower_semantics(
        authored,
        limits=LIMITS,
        chrome=DEFAULT_CHROME,
        localization=NEUTRAL,
        session=PresentationState(),
    )

    assert [type(node).__name__ for node in lowered.nodes] == [
        "Text",
        "Heading",
        "Footer",
        "Code",
        "Lines",
        "RoutedButton",
        "RoutedSelect",
        "Thumbnail",
        "Content",
        "LinkButton",
        "SelectMenu",
        "Gallery",
        "Card",
    ]
    assert [name for node in lowered.nodes for name in _deferred_text_in(node)] == []


def test_unknown_renderables_are_rejected_with_their_path() -> None:
    class Unregistered(Renderable[DiscordTarget]):
        pass

    # Reject unknown nodes at their authored path before they enter the primitive stream.
    with pytest.raises(LayoutInvariantError, match=r"\$\.0: Unregistered is neither"):
        lower_semantics(
            [cast(Any, Unregistered())],
            limits=LIMITS,
            chrome=DEFAULT_CHROME,
            localization=NEUTRAL,
            session=PresentationState(),
        )


@pytest.mark.parametrize("make", [Text, Heading, Footer, Code, lambda content: Lines(lines=(content,))])
def test_a_page_footer_is_resolved_on_every_text_primitive(make: Any) -> None:
    # Only Lines used to resolve its footer, so a paged traceback in a code block failed to plan.
    node = make(tr(t"test-hello = hello"))
    paged = replace(node, overflow=Paginate(key="pages", footer=lambda page, pages: tr(t"test-page = page {page}")))

    lowered = lower_semantics(
        [paged], limits=LIMITS, chrome=DEFAULT_CHROME, localization=NEUTRAL, session=PresentationState()
    )

    overflow = getattr(lowered.nodes[0], "overflow", None)
    assert isinstance(overflow, Paginate)
    footer = overflow.footer
    assert footer is not None
    assert footer(2, 3) == "page 2"
