"""Visible downloads hoist assets without losing portable scene identity."""

import io
import threading
from pathlib import Path
from typing import override

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui import scene
from squid_ui.document import DocumentLike
from squid_ui.html import DiscordPreviewRenderer
from squid_ui.runtime.component import Component
from squid_ui_discord import DISCORD_V2_DPY27, Everyone, MessageRoot, delivery
from squid_ui_discord.renderer import V2Renderer


def _inline() -> sl.document.Asset:
    return sl.document.Asset("report", "report.txt", "text/plain", sl.document.InlineAsset(b"full report"))


def test_download_factory_hoists_its_asset_and_preserves_file_metadata() -> None:
    asset = _inline()
    node = sl.download(asset, label="Report", key="report-download", description="Generated now")

    result = sl.planning.plan(node, target=DISCORD_V2_DPY27)

    assert isinstance(node, sl.semantic.Download)
    assert result.scene.components_v2.children == (
        scene.Text("Report\nGenerated now"),
        scene.File("report", "report.txt", "text/plain"),
    )
    assert result.scene.assets == (sl.scene.Asset("report", "report.txt", "text/plain"),)
    assert result.resources["asset:report"] is asset


def test_download_uses_localized_chrome_when_the_explicit_label_is_none() -> None:
    result = sl.planning.plan(sl.download(_inline(), label=None, key="report-download"), target=DISCORD_V2_DPY27)

    assert result.scene.components_v2.children[0] == scene.Text("Download")


def test_equal_asset_keys_deduplicate_but_conflicting_assets_raise() -> None:
    asset = _inline()
    node = sl.download(asset, label="Report", key="report-download")

    result = sl.planning.plan(sl.Document((node,), (asset,)), target=DISCORD_V2_DPY27)
    assert result.scene.assets == (sl.scene.Asset("report", "report.txt", "text/plain"),)

    conflicting = sl.document.Asset("report", "other.txt", "text/plain", sl.document.InlineAsset(b"other"))
    with pytest.raises(sl.errors.LayoutInvariantError, match="identifies two different assets"):
        sl.planning.plan(sl.Document((node,), (conflicting,)), target=DISCORD_V2_DPY27)


def test_scene_file_codec_round_trips() -> None:
    document = sl.planning.plan(
        sl.download(_inline(), label="Report", key="report-download"), target=DISCORD_V2_DPY27
    ).scene

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document
    assert scene.Codec.to_dict(document)["body"]["children"][1] == {
        "kind": "file",
        "asset_key": "report",
        "name": "report.txt",
        "media_type": "text/plain",
        "spoiler": False,
    }


def test_discord_renderer_draws_an_attachment_file_or_url_link() -> None:
    inline = sl.planning.plan(sl.download(_inline(), label="Report", key="report-download"), target=DISCORD_V2_DPY27)
    inline_view = V2Renderer().view(inline.scene, plan=inline)
    assert any(isinstance(item, discord.ui.File) for item in inline_view.walk_children())

    stored = sl.document.Asset(
        "report", "report.txt", "text/plain", sl.document.StoredAsset("https://example.com/report.txt")
    )
    linked = sl.planning.plan(sl.download(stored, label="Report", key="report-download"), target=DISCORD_V2_DPY27)
    linked_view = V2Renderer().view(linked.scene, plan=linked)
    link = next(item for item in linked_view.walk_children() if isinstance(item, discord.ui.Button))
    assert link.url == "https://example.com/report.txt"


class _DownloadComponent(Component[sl.ComponentsV2Target]):
    def __init__(self, asset: sl.document.Asset) -> None:
        self.asset = asset

    @override
    def render(self) -> DocumentLike[sl.ComponentsV2Target]:
        return sl.download(self.asset, label="Report", key="report-download")


async def _send(message_root: MessageRoot) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    delivered: list[tuple[discord.ui.LayoutView, list[discord.File]]] = []

    async def destination(payload: squid_ui_discord.message_payload.MessagePayload) -> delivery.DeliveryResult:
        # Materializing the files is the destination's job, so this is where an asset the
        # host never resolved is refused.
        delivered.append((payload.layout, payload.build_files()))
        return delivery.DeliveryResult(None, None)

    await message_root.send(destination)
    return delivered[0]


async def test_message_root_attaches_inline_bytes_and_does_not_attach_url_assets() -> None:
    _view, files = await _send(MessageRoot(_DownloadComponent(_inline()), access=Everyone(), timeout=None))
    assert len(files) == 1
    assert files[0].filename == "report.txt"
    assert files[0].fp.read() == b"full report"

    stored = sl.document.Asset(
        "report", "report.txt", "text/plain", sl.document.StoredAsset("https://example.com/report.txt")
    )
    view, linked_files = await _send(MessageRoot(_DownloadComponent(stored), access=Everyone(), timeout=None))
    assert linked_files == []
    assert any(isinstance(item, discord.ui.Button) and item.url for item in view.walk_children())


async def test_message_root_keeps_raising_for_non_url_stored_references() -> None:
    stored = sl.document.Asset("report", "report.txt", "text/plain", sl.document.StoredAsset("reports/current"))

    with pytest.raises(TypeError, match="needs a host resolver"):
        await _send(MessageRoot(_DownloadComponent(stored), access=Everyone(), timeout=None))


def test_html_renderer_emits_data_links_resolver_links_and_visible_placeholders() -> None:
    result = sl.planning.plan(sl.download(_inline(), label="Report", key="report-download"), target=DISCORD_V2_DPY27)

    rendered = DiscordPreviewRenderer().draw(result.scene, plan=result)
    assert 'href="data:text/plain;base64,ZnVsbCByZXBvcnQ="' in rendered
    assert 'download="report.txt"' in rendered

    resolved = DiscordPreviewRenderer(asset_resolver=lambda _asset: "https://example.com/report.txt").draw(result.scene)
    assert 'href="https://example.com/report.txt"' in resolved

    unresolved = DiscordPreviewRenderer().draw(result.scene)
    assert 'class="squid-button squid-file" aria-disabled="true"' in unresolved
    assert "report.txt" in unresolved


async def test_stream_delivery_reads_off_loop_and_replays_after_close() -> None:
    main_thread = threading.get_ident()
    read_threads: list[int] = []

    class Stream(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            read_threads.append(threading.get_ident())
            return super().read(size)

    stream = Stream(b"prefix-image")
    stream.seek(7)
    asset = sl.file(stream, filename="image.png")
    payload = squid_ui_discord.render_static(
        sl.stack(sl.media(asset, key="preview"), sl.download(asset, key="download"))
    )
    assert read_threads == []
    first = await payload.prepare_files()
    try:
        assert len(first) == 1
        assert first[0].fp.read() == b"image"
        assert stream.tell() == 7
        assert read_threads and main_thread not in read_threads
        stream.close()
        second = await payload.prepare_files()
        try:
            assert second[0].fp.read() == b"image"
            assert second[0] is not first[0]
            assert len(read_threads) == 1
        finally:
            for file in second:
                file.close()
    finally:
        for file in first:
            file.close()


@pytest.mark.parametrize("classic", [False, True])
def test_url_download_needs_no_upload(classic: bool) -> None:
    node = sl.download("https://example.org/report.txt", label="Report", key="report")
    rendered = squid_ui_discord.classic.render_static(node) if classic else squid_ui_discord.render_static(node)
    assert rendered.build_files() == []


def test_path_media_and_download_share_one_upload(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    document = sl.stack(sl.media(path, key="preview"), sl.download(path, key="download"))
    rendered = squid_ui_discord.render_static(document)
    path.write_bytes(b"image")
    files = rendered.build_files()
    try:
        assert len(files) == 1
        assert files[0].filename == "image.png"
        assert files[0].fp.read() == b"image"
    finally:
        for file in files:
            file.close()


def test_different_files_cannot_share_an_attachment_name() -> None:
    document = sl.media(
        sl.media_item(b"first", filename="image.png"),
        sl.media_item(b"second", filename="image.png"),
        key="gallery",
    )
    with pytest.raises(sl.errors.LayoutInvariantError, match="share filename"):
        squid_ui_discord.render_static(document)
