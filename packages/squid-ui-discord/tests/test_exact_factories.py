"""Exact Discord factory normalization and rendering parity."""

# ruff: noqa: FBT003 - positional False is the conditional-child API under test

from unittest.mock import AsyncMock

import pytest

import squid_ui_discord as sd
from squid_ui.assets import Asset, InlineAsset
from squid_ui.primitives import (
    ActionStyle,
    Button,
    Card,
    CardAuthor,
    CardField,
    CardFooter,
    CardMedia,
    Content,
    ControlGroup,
    File,
    Gallery,
    GalleryItem,
    Heading,
    Panel,
    Row,
    Section,
    Sep,
    Text,
    Thumbnail,
)


def test_v2_factories_build_the_exact_primitive_ir() -> None:
    handler = AsyncMock()
    press = sd.v2.button("Open", handler, key="open", style=ActionStyle.PRIMARY)
    accessory = sd.v2.thumbnail("https://example.com/thumb.png", description="Preview")

    assert press == Button("Open", handler, "open", ActionStyle.PRIMARY)
    assert sd.v2.section(sd.v2.heading("Title"), "Body", None, False, accessory=accessory) == Section(
        (Heading("Title"), Text("Body")),
        Thumbnail("https://example.com/thumb.png", "Preview"),
    )
    assert sd.v2.panel("Body", None, False, press, accent=0x123456) == Panel(
        (Text("Body"), press),
        accent=0x123456,
    )
    assert sd.v2.separator(large=True) == Sep(large=True)
    assert sd.v2.gallery("https://example.com/a.png", sd.v2.gallery_item("b.png", spoiler=True)) == Gallery(
        (GalleryItem("https://example.com/a.png"), GalleryItem("b.png", spoiler=True))
    )


def test_v2_file_factory_reads_the_asset_metadata() -> None:
    asset = Asset("report", "report.json", "application/json", InlineAsset(b"{}"))

    assert sd.v2.file(asset, spoiler=True) == File("report", "report.json", "application/json", spoiler=True)


def test_classic_factories_build_the_exact_primitive_ir() -> None:
    field = sd.classic.card_field("Name", "Value", inline=True)
    author = sd.classic.card_author("Author", url="https://example.com")
    footer = sd.classic.card_footer("Footer", icon_url="https://example.com/icon.png")
    image = sd.classic.card_media("https://example.com/image.png", description="Image")

    assert sd.classic.content("Preview") == Content("Preview")
    assert sd.classic.card(
        "Description",
        None,
        False,
        title="Card",
        fields=(field,),
        author=author,
        footer=footer,
        image=image,
    ) == Card(
        (Text("Description"),),
        title="Card",
        fields=(CardField("Name", "Value", inline=True),),
        author=CardAuthor("Author", url="https://example.com"),
        footer=CardFooter("Footer", icon_url="https://example.com/icon.png"),
        image=CardMedia("https://example.com/image.png", description="Image"),
    )


def test_control_factories_distinguish_exact_rows_from_auto_layout() -> None:
    one = sd.v2.link_button("One", "https://example.com/1")
    two = sd.v2.link_button("Two", "https://example.com/2")

    assert sd.v2.row(one, None, False, two) == Row((one, two))
    assert sd.classic.controls(one, two) == ControlGroup((one, two))


@pytest.mark.parametrize("factory", [sd.v2.panel, sd.classic.card, sd.v2.row])
def test_factories_reject_true_as_content(factory: object) -> None:
    with pytest.raises(TypeError, match=r"True is not content|not a Discord button"):
        factory(True)  # type: ignore[operator]


def test_exact_factory_output_renders_in_each_mode() -> None:
    v2_payload = sd.render_static(sd.v2.panel("V2"))
    classic_payload = sd.classic.render_static((sd.classic.content("Preview"), sd.classic.card("Classic")))

    assert v2_payload.mode is sd.MessageMode.COMPONENTS_V2
    assert classic_payload.mode is sd.MessageMode.CLASSIC
