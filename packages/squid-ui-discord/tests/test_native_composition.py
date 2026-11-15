"""The installed native-item adapter participates in certified region pages."""

import discord
import pytest

import squid_ui as sl
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import plan
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.resources import Axis
from squid_ui.primitives import Panel, Text
from squid_ui_discord import DISCORD_V2_DPY27
from squid_ui_discord.renderer import V2Renderer
from squid_ui_discord.target import NativeItem, v2


def test_native_items_prepare_once_and_publish_only_visible_resources() -> None:
    calls: list[str] = []

    def factory(label: str) -> discord.ui.Container:
        calls.append(label)
        return discord.ui.Container(*(discord.ui.TextDisplay(label) for _ in range(10)))

    result = plan(
        sl.region(
            NativeItem(lambda: factory("first"), fallback=Text("first")),
            NativeItem(lambda: factory("second"), fallback=Text("second")),
            key="native",
            title="Native",
        ),
        target=v2(limits=V2Limits(total_components=16)),
    )
    assert calls == ["first", "second"]
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    assert len(result.allocation.regions[identity].cuts) == 2
    for page, label in enumerate(("first", "second")):
        selected = result.for_page({identity: page})
        native = [value for value in selected.resources.values() if isinstance(value, discord.ui.Container)]
        assert len(native) == 1
        assert all(isinstance(child, discord.ui.TextDisplay) and child.content == label for child in native[0].children)
        view = V2Renderer().view(selected.scene, plan=selected)
        assert native[0] in view.children
    assert calls == ["first", "second"]


def test_native_media_deduplicates_its_declared_attachment() -> None:
    asset = sl.file(b"image", filename="image.png")
    image = NativeItem(
        lambda: discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://image.png")),
        fallback=Text("Image"),
    )
    result = plan(sl.Document((image, image), assets=(asset,)), target=DISCORD_V2_DPY27)
    assert result.allocation is not None
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.resources[f"asset:{asset.key}"] is asset
    assert len(result.scene.assets) == 1


def test_native_container_cannot_hide_a_nested_container() -> None:
    item = NativeItem(lambda: discord.ui.Container(discord.ui.TextDisplay("native")), fallback=Text("native"))
    with pytest.raises(sl.planning.JointInfeasibleError):
        plan(Panel((item,)), target=DISCORD_V2_DPY27)


def test_native_attachment_requires_a_document_declaration() -> None:
    item = NativeItem(lambda: discord.ui.File("attachment://missing.txt"), fallback=Text("file"))
    with pytest.raises(LayoutInvariantError, match="must be declared"):
        plan(item, target=DISCORD_V2_DPY27)


def test_native_local_limits_are_independently_checked() -> None:
    item = NativeItem(
        lambda: discord.ui.MediaGallery(discord.MediaGalleryItem("https://example.invalid/a", description="long")),
        fallback=Text("image"),
    )
    with pytest.raises(sl.errors.LimitViolationError, match="description"):
        plan(item, target=v2(limits=V2Limits(gallery_item_description=3)))
