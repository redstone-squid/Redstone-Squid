"""Visible downloads hoist assets without losing portable scene identity."""

import discord
import pytest

import squid_ui_discord
import squid_ui as sl
from squid_ui_discord import DISCORD_V2_DPY27, Everyone, Mount, delivery
from squid_ui_discord.renderer import V2Renderer
from squid_ui import scene
from squid_ui.html import Renderer as HtmlRenderer
from squid_ui.runtime.component import Component, RenderResult


def _inline() -> sl.document.Asset:
    return sl.document.Asset("report", "report.txt", "text/plain", sl.document.InlineAsset(b"full report"))


def test_download_factory_hoists_its_asset_and_preserves_file_metadata() -> None:
    asset = _inline()
    node = sl.download("Report", asset, key="report-download", description="Generated now")

    result = sl.planning.plan(node, target=DISCORD_V2_DPY27)

    assert isinstance(node, sl.semantic.Download)
    assert result.scene.components_v2.children == (
        scene.Text("Report\nGenerated now"),
        scene.File("report", "report.txt", "text/plain"),
    )
    assert result.scene.assets == (sl.scene.Asset("report", "report.txt", "text/plain"),)
    assert result.resources["asset:report"] is asset


def test_download_uses_localized_chrome_when_the_explicit_label_is_none() -> None:
    result = sl.planning.plan(sl.download(None, _inline(), key="report-download"), target=DISCORD_V2_DPY27)

    assert result.scene.components_v2.children[0] == scene.Text("Download")


def test_equal_asset_keys_deduplicate_but_conflicting_assets_raise() -> None:
    asset = _inline()
    node = sl.download("Report", asset, key="report-download")

    result = sl.planning.plan(sl.Document((node,), (asset,)), target=DISCORD_V2_DPY27)
    assert result.scene.assets == (sl.scene.Asset("report", "report.txt", "text/plain"),)

    conflicting = sl.document.Asset("report", "other.txt", "text/plain", sl.document.InlineAsset(b"other"))
    with pytest.raises(sl.errors.LayoutInvariantError, match="identifies two different assets"):
        sl.planning.plan(sl.Document((node,), (conflicting,)), target=DISCORD_V2_DPY27)


def test_scene_file_codec_round_trips() -> None:
    document = sl.planning.plan(sl.download("Report", _inline(), key="report-download"), target=DISCORD_V2_DPY27).scene

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document
    assert scene.Codec.to_dict(document)["body"]["children"][1] == {
        "kind": "file",
        "asset_key": "report",
        "name": "report.txt",
        "media_type": "text/plain",
        "spoiler": False,
    }


def test_discord_renderer_draws_an_attachment_file_or_url_link() -> None:
    inline = sl.planning.plan(sl.download("Report", _inline(), key="report-download"), target=DISCORD_V2_DPY27)
    inline_view = V2Renderer().view(inline.scene, plan=inline)
    assert any(isinstance(item, discord.ui.File) for item in inline_view.walk_children())

    stored = sl.document.Asset(
        "report", "report.txt", "text/plain", sl.document.StoredAsset("https://example.com/report.txt")
    )
    linked = sl.planning.plan(sl.download("Report", stored, key="report-download"), target=DISCORD_V2_DPY27)
    linked_view = V2Renderer().view(linked.scene, plan=linked)
    link = next(item for item in linked_view.walk_children() if isinstance(item, discord.ui.Button))
    assert link.url == "https://example.com/report.txt"


class _DownloadComponent(Component):
    def __init__(self, asset: sl.document.Asset) -> None:
        self.asset = asset

    def render(self) -> RenderResult:
        return sl.download("Report", self.asset, key="report-download")


async def _send(mount: Mount) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    delivered: list[tuple[discord.ui.LayoutView, list[discord.File]]] = []

    async def destination(presentation: squid_ui_discord.presentation.DiscordPresentation) -> delivery.DeliveryResult:
        # Materializing the files is the destination's job, so this is where an asset the
        # host never resolved is refused.
        delivered.append((presentation.layout, presentation.build_files()))
        return delivery.DeliveryResult(None, None)

    await mount.send(destination)
    return delivered[0]


async def test_mount_attaches_inline_bytes_and_does_not_attach_url_assets() -> None:
    _view, files = await _send(Mount(_DownloadComponent(_inline()), access=Everyone(), timeout=None))
    assert len(files) == 1
    assert files[0].filename == "report.txt"
    assert files[0].fp.read() == b"full report"

    stored = sl.document.Asset(
        "report", "report.txt", "text/plain", sl.document.StoredAsset("https://example.com/report.txt")
    )
    view, linked_files = await _send(Mount(_DownloadComponent(stored), access=Everyone(), timeout=None))
    assert linked_files == []
    assert any(isinstance(item, discord.ui.Button) and item.url for item in view.walk_children())


async def test_mount_keeps_raising_for_non_url_stored_references() -> None:
    stored = sl.document.Asset("report", "report.txt", "text/plain", sl.document.StoredAsset("reports/current"))

    with pytest.raises(TypeError, match="needs a host resolver"):
        await _send(Mount(_DownloadComponent(stored), access=Everyone(), timeout=None))


def test_html_renderer_emits_data_links_resolver_links_and_visible_placeholders() -> None:
    result = sl.planning.plan(sl.download("Report", _inline(), key="report-download"), target=DISCORD_V2_DPY27)

    rendered = HtmlRenderer().draw(result.scene, plan=result)
    assert 'href="data:text/plain;base64,ZnVsbCByZXBvcnQ="' in rendered
    assert 'download="report.txt"' in rendered

    resolved = HtmlRenderer(asset_resolver=lambda _asset: "https://example.com/report.txt").draw(result.scene)
    assert 'href="https://example.com/report.txt"' in resolved

    unresolved = HtmlRenderer().draw(result.scene)
    assert 'class="squid-button squid-file" aria-disabled="true"' in unresolved
    assert "report.txt" in unresolved
