"""Native galleries keep exact grouping and share declared attachment resources."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.authoring.document import Document
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantic_correspondence import check_alternative
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.primitives import nodes as native
from squid_ui.target_types import DiscordAdapter

ADAPTER = AdapterProfile(DiscordAdapter, "native-media", "1")
TARGET = components_v2_target(ADAPTER)


@pytest.mark.parametrize("explicit_url", [False, True])
def test_native_gallery_and_file_share_one_attachment_without_losing_metadata(explicit_url: bool) -> None:
    asset = sl.file(b"image", filename="preview.png", media_type="image/png")
    item = native.GalleryItem(
        f"attachment://{asset.name}" if explicit_url else "", sl.strong("Preview"), spoiler=True, asset_name=asset.name
    )
    node = sl.region(
        native.Gallery((item,)), native.File(asset.key, asset.name, asset.media_type, spoiler=True), key="r", title="R"
    )
    result = plan(Document((node,), assets=(asset,)), target=TARGET, strict=True)
    assert result.allocation is not None
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.cost.get(Axis.COMPONENTS) == 2
    assert result.scene.body.children == (
        scene.Gallery(
            (scene.GalleryItem(f"attachment://{asset.name}", "Preview", spoiler=True, asset_name=asset.name),)
        ),
        scene.File(asset.key, asset.name, asset.media_type, spoiler=True),
    )
    assert not result.report.events


def test_collection_packs_target_galleries_and_can_page_at_image_boundaries() -> None:
    node = native.MediaCollection(tuple(native.GalleryItem(f"https://example.com/{i}.png", str(i)) for i in range(5)))
    target = components_v2_target(ADAPTER, limits=V2Limits(gallery_items=2))
    identity = OccurrenceId(("root", "r"))
    document = sl.region(node, key="r", title="R")
    complete = plan(document, target=target)
    assert [len(gallery.items) for gallery in complete.scene.body.children if isinstance(gallery, scene.Gallery)] == [
        2,
        2,
        1,
    ]
    result = plan(
        document, target=target, composition=CompositionOptions(automatic=True, partitions={identity: (3, 5)})
    )
    assert result.allocation is not None
    descriptions = []
    for index in range(2):
        page = result.for_page({identity: index})
        descriptions.extend(
            item.description
            for gallery in page.scene.body.children
            if isinstance(gallery, scene.Gallery)
            for item in gallery.items
        )
    assert descriptions == [str(index) for index in range(5)]


@pytest.mark.parametrize("count", [0, 3])
def test_exact_gallery_cannot_be_split_or_empty_to_evade_native_limits(count: int) -> None:
    node = native.Gallery(tuple(f"https://example.com/{i}.png" for i in range(count)))
    with pytest.raises(JointInfeasibleError):
        plan(
            sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER, limits=V2Limits(gallery_items=2))
        )


def test_checked_alternative_cannot_replace_an_exact_gallery_with_a_collection() -> None:
    source = native.Gallery(("https://example.com/1.png", "https://example.com/2.png"))
    choice = sl.alternative(native.MediaCollection(source.items), key="split", fidelity=sl.equivalent())
    with pytest.raises(LayoutInvariantError, match="preserve the Gallery boundary"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)


@pytest.mark.parametrize("mismatch", ["missing", "name", "media_type", "gallery_url", "gallery_name"])
def test_native_media_rejects_missing_or_mismatched_assets(mismatch: str) -> None:
    asset = sl.file(b"image", filename="preview.png", media_type="image/png")
    file = native.File(asset.key, asset.name, asset.media_type)
    node: native.Node = file
    if mismatch == "name":
        node = replace(file, name="wrong.png")
    elif mismatch == "media_type":
        node = replace(file, media_type="text/plain")
    elif mismatch.startswith("gallery"):
        item = native.GalleryItem(
            "https://example.com/wrong.png" if mismatch == "gallery_url" else f"attachment://{asset.name}",
            asset_name=asset.name if mismatch == "gallery_url" else "wrong.png",
        )
        node = native.Gallery((item,))
    document = Document((sl.region(node, key="r", title="R"),), assets=() if mismatch == "missing" else (asset,))
    with pytest.raises(JointInfeasibleError):
        plan(document, target=TARGET)


def test_changed_gallery_metadata_invalidates_retained_allocation() -> None:
    session = PresentationState()
    original = native.GalleryItem("https://example.com/image.png", "Before")
    first = plan(sl.region(native.Gallery((original,)), key="r", title="R"), target=TARGET, session=session)
    session.apply_updates(first.session_updates)
    second = plan(
        sl.region(native.Gallery((replace(original, description="After", spoiler=True),)), key="r", title="R"),
        target=TARGET,
        session=session,
    )
    assert second.allocation is not first.allocation
    assert second.scene.body.children == (scene.Gallery((scene.GalleryItem(original.url, "After", spoiler=True),)),)


def test_native_gallery_reports_its_classic_capability_boundary() -> None:
    target: Target[Any, Any, Any, Any] = classic_target(ADAPTER)
    with pytest.raises(JointUnsupportedError) as error:
        plan(
            sl.region(native.Gallery(("https://example.com/1.png",)), key="r", title="R"),
            target=target,
        )
    assert "native Components V2 layout on another target" in str(error.value.diagnostics)
