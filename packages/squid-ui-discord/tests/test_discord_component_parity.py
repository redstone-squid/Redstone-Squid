"""Discord component fields that must survive planning and drawing unchanged."""

import discord
import pytest

from squid_ui_discord import DISCORD_V2_DPY27, classic, render_static
from squid_ui_discord.testing import without_capabilities
from squid_ui import scene
from squid_ui.emoji import Emoji
from squid_ui.errors import LayoutInvariantError
from squid_ui.html import Renderer as HtmlRenderer
from squid_ui.planning import plan
from squid_ui.primitives import (
    Gallery,
    GalleryItem,
    LinkButton,
    Option,
    PremiumButton,
    RoutedSelect,
    Row,
    Text,
    Variant,
    Variants,
)


def test_v2_draws_premium_links_select_emoji_and_media_metadata() -> None:
    presentation = render_static(
        [
            Row(
                (
                    PremiumButton(42),
                    LinkButton(None, "https://example.invalid", emoji=Emoji("wave", 7), disabled=True),
                )
            ),
            RoutedSelect((Option("One", "1", emoji="1️⃣"),), "pick"),
            Gallery((GalleryItem("https://example.invalid/image.png", "accessible preview", spoiler=True),)),
        ],
        target=DISCORD_V2_DPY27,
    )
    row = presentation.layout.children[0]
    assert isinstance(row, discord.ui.ActionRow)
    premium, link = row.children
    select_row = presentation.layout.children[1]
    gallery = presentation.layout.children[2]
    assert isinstance(premium, discord.ui.Button)
    assert isinstance(link, discord.ui.Button)
    assert isinstance(select_row, discord.ui.ActionRow)
    select = select_row.children[0]
    assert isinstance(select, discord.ui.Select)
    assert isinstance(gallery, discord.ui.MediaGallery)

    assert premium.sku_id == 42
    assert link.label is None and link.emoji is not None and link.emoji.id == 7 and link.disabled is True
    assert select.options[0].emoji is not None and select.options[0].emoji.name == "1️⃣"
    assert gallery.items[0].description == "accessible preview"
    assert gallery.items[0].spoiler is True


def test_classic_draws_premium_and_disabled_emoji_links() -> None:
    presentation = classic.render_static(
        [Row((PremiumButton(42), LinkButton(None, "https://example.invalid", emoji="🔗", disabled=True)))]
    )
    assert presentation.view is not None
    premium, link = presentation.view.children
    assert isinstance(premium, discord.ui.Button)
    assert isinstance(link, discord.ui.Button)

    assert premium.sku_id == 42
    assert link.label is None and link.emoji is not None and link.emoji.name == "🔗" and link.disabled is True


def test_html_marks_premium_metadata_and_spoilers_accessibly() -> None:
    document = scene.Document(
        1,
        "discord.components-v2",
        1,
        scene.ComponentsV2(
            (
                scene.Panel(
                    (
                        scene.PremiumButton(42),
                        scene.Gallery(
                            (scene.GalleryItem("https://example.invalid/image.png", "preview", spoiler=True),)
                        ),
                    ),
                    spoiler=True,
                ),
            )
        ),
    )

    rendered = HtmlRenderer().draw(document)

    assert 'data-sku-id="42"' in rendered
    assert 'alt="preview"' in rendered
    assert "squid-spoiler" in rendered and 'tabindex="0"' in rendered


def test_non_discord_target_requires_explicit_premium_fallback() -> None:
    target = without_capabilities(DISCORD_V2_DPY27, "actions.discord.premium")

    with pytest.raises(LayoutInvariantError, match="explicit Variants fallback"):
        plan(Row((PremiumButton(42),)), target=target)

    result = plan(
        Variants(
            (
                Variant((Row((PremiumButton(42),)),), requires=frozenset({"actions.discord.premium"})),
                Variant((Text("Purchase unavailable"),)),
            )
        ),
        target=target,
    )

    assert result.scene.components_v2.children == (scene.Text("Purchase unavailable"),)
