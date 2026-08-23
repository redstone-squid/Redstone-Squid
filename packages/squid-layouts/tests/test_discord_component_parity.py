"""Discord component fields that must survive planning and drawing unchanged."""


from squid_layouts.discord import V2_TARGET, classic, render_static
from squid_layouts.emoji import Emoji
from squid_layouts.html import Renderer as HtmlRenderer
from squid_layouts.primitives import Gallery, GalleryItem, LinkButton, Option, PremiumButton, RoutedSelect, Row
from squid_layouts.scene.model import (
    SceneComponentsV2,
    SceneDocument,
    SceneGallery,
    SceneGalleryItem,
    ScenePanel,
    ScenePremiumButton,
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
        target=V2_TARGET,
    )
    row = presentation.layout.children[0]
    premium, link = row.children
    select = presentation.layout.children[1].children[0]
    gallery = presentation.layout.children[2]

    assert premium.sku_id == 42
    assert link.label is None and link.emoji.id == 7 and link.disabled is True
    assert select.options[0].emoji.name == "1️⃣"
    assert gallery.items[0].description == "accessible preview"
    assert gallery.items[0].spoiler is True


def test_classic_draws_premium_and_disabled_emoji_links() -> None:
    presentation = classic.render_static(
        [Row((PremiumButton(42), LinkButton(None, "https://example.invalid", emoji="🔗", disabled=True)))]
    )
    assert presentation.view is not None
    premium, link = presentation.view.children

    assert premium.sku_id == 42
    assert link.label is None and link.emoji.name == "🔗" and link.disabled is True


def test_html_marks_premium_metadata_and_spoilers_accessibly() -> None:
    scene = SceneDocument(
        1,
        "discord.components-v2",
        1,
        SceneComponentsV2(
            (
                ScenePanel(
                    (
                        ScenePremiumButton(42),
                        SceneGallery((SceneGalleryItem("https://example.invalid/image.png", "preview", spoiler=True),)),
                    ),
                    spoiler=True,
                ),
            )
        ),
    )

    rendered = HtmlRenderer().draw(scene)

    assert 'data-sku-id="42"' in rendered
    assert 'alt="preview"' in rendered
    assert "squid-spoiler" in rendered and 'tabindex="0"' in rendered
