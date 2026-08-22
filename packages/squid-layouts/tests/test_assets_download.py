"""Visible downloads hoist assets without losing portable scene identity."""

from collections.abc import Sequence

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import DEFAULT_TARGET, Mount, Renderer, delivery
from squid_layouts.html import Renderer as HtmlRenderer
from squid_layouts.runtime.component import Component, RenderResult
from squid_layouts.scene import Codec
from squid_layouts.scene.model import SceneFile, SceneText


def _inline() -> sl.Asset:
    return sl.Asset("report", "report.txt", "text/plain", sl.InlineAsset(b"full report"))


def test_download_factory_hoists_its_asset_and_preserves_file_metadata() -> None:
    asset = _inline()
    node = sl.download("Report", asset, key="report-download", description="Generated now")

    result = sl.plan(node, target=DEFAULT_TARGET)

    assert isinstance(node, sl.Download)
    assert result.scene.children == (
        SceneText("Report\nGenerated now"),
        SceneFile("report", "report.txt", "text/plain"),
    )
    assert result.scene.assets == (sl.scene.SceneAsset("report", "report.txt", "text/plain"),)
    assert result.resources["asset:report"] is asset


def test_download_uses_localized_chrome_when_the_explicit_label_is_none() -> None:
    result = sl.plan(sl.download(None, _inline(), key="report-download"), target=DEFAULT_TARGET)

    assert result.scene.children[0] == SceneText("Download")


def test_equal_asset_keys_deduplicate_but_conflicting_assets_raise() -> None:
    asset = _inline()
    node = sl.download("Report", asset, key="report-download")

    result = sl.plan(sl.Document((node,), (asset,)), target=DEFAULT_TARGET)
    assert result.scene.assets == (sl.scene.SceneAsset("report", "report.txt", "text/plain"),)

    conflicting = sl.Asset("report", "other.txt", "text/plain", sl.InlineAsset(b"other"))
    with pytest.raises(sl.LayoutInvariantError, match="identifies two different assets"):
        sl.plan(sl.Document((node,), (conflicting,)), target=DEFAULT_TARGET)


def test_scene_file_codec_round_trips() -> None:
    scene = sl.plan(sl.download("Report", _inline(), key="report-download"), target=DEFAULT_TARGET).scene

    assert Codec.loads(Codec.dumps(scene)) == scene
    assert Codec.to_dict(scene)["children"][1] == {
        "kind": "file",
        "asset_key": "report",
        "name": "report.txt",
        "media_type": "text/plain",
    }


def test_discord_renderer_draws_an_attachment_file_or_url_link() -> None:
    inline = sl.plan(sl.download("Report", _inline(), key="report-download"), target=DEFAULT_TARGET)
    inline_view = Renderer().draw(inline.scene, plan=inline)
    assert any(isinstance(item, discord.ui.File) for item in inline_view.walk_children())

    stored = sl.Asset("report", "report.txt", "text/plain", sl.StoredAsset("https://example.com/report.txt"))
    linked = sl.plan(sl.download("Report", stored, key="report-download"), target=DEFAULT_TARGET)
    linked_view = Renderer().draw(linked.scene, plan=linked)
    link = next(item for item in linked_view.walk_children() if isinstance(item, discord.ui.Button))
    assert link.url == "https://example.com/report.txt"


class _DownloadComponent(Component):
    def __init__(self, asset: sl.Asset) -> None:
        self.asset = asset

    def render(self) -> RenderResult:
        return sl.download("Report", self.asset, key="report-download")


async def _send(mount: Mount) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    delivered: list[tuple[discord.ui.LayoutView, list[discord.File]]] = []

    async def destination(view: discord.ui.LayoutView, files: Sequence[discord.File]) -> delivery.DeliveryReceipt:
        delivered.append((view, list(files)))
        return delivery.DeliveryReceipt(None, None)

    await mount.send(destination)
    return delivered[0]


async def test_mount_attaches_inline_bytes_and_does_not_attach_url_assets() -> None:
    _view, files = await _send(Mount(_DownloadComponent(_inline()), timeout=None))
    assert len(files) == 1
    assert files[0].filename == "report.txt"
    assert files[0].fp.read() == b"full report"

    stored = sl.Asset("report", "report.txt", "text/plain", sl.StoredAsset("https://example.com/report.txt"))
    view, linked_files = await _send(Mount(_DownloadComponent(stored), timeout=None))
    assert linked_files == []
    assert any(isinstance(item, discord.ui.Button) and item.url for item in view.walk_children())


async def test_mount_keeps_raising_for_non_url_stored_references() -> None:
    stored = sl.Asset("report", "report.txt", "text/plain", sl.StoredAsset("reports/current"))

    with pytest.raises(TypeError, match="needs a host resolver"):
        await _send(Mount(_DownloadComponent(stored), timeout=None))


def test_html_renderer_emits_data_links_resolver_links_and_visible_placeholders() -> None:
    result = sl.plan(sl.download("Report", _inline(), key="report-download"), target=DEFAULT_TARGET)

    rendered = HtmlRenderer().draw(result.scene, plan=result)
    assert 'href="data:text/plain;base64,ZnVsbCByZXBvcnQ="' in rendered
    assert 'download="report.txt"' in rendered

    resolved = HtmlRenderer(asset_resolver=lambda _asset: "https://example.com/report.txt").draw(result.scene)
    assert 'href="https://example.com/report.txt"' in resolved

    unresolved = HtmlRenderer().draw(result.scene)
    assert 'class="squid-button squid-file" aria-disabled="true"' in unresolved
    assert "report.txt" in unresolved
