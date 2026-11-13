"""Ordinary file inputs normalize without doing I/O during layout construction."""

import io
from pathlib import Path

import pytest

import squid_ui as sl
from squid_ui.assets import InlineAsset, LocalAsset


def test_path_is_lazy_and_snapshots_on_first_read(tmp_path: Path) -> None:
    path = tmp_path / "preview.png"
    asset = sl.file(path)
    assert asset.name == "preview.png"
    assert asset.media_type == "image/png"
    path.write_bytes(b"first")
    assert asset.read() == b"first"
    path.write_bytes(b"second")
    assert asset.read() == b"first"
    assert sl.file(path).read() == b"second"


def test_binary_stream_snapshot_preserves_position_and_survives_close() -> None:
    stream = io.BytesIO(b"prefix-image")
    stream.seek(7)
    asset = sl.file(stream, filename="image.png")
    assert isinstance(asset.source, LocalAsset)
    assert asset.read() == b"image"
    assert stream.tell() == 7
    assert not stream.closed
    stream.close()
    assert asset.read() == b"image"


def test_nonseekable_stream_can_be_reused_through_one_file() -> None:
    class Stream(io.BytesIO):
        def seekable(self) -> bool:
            return False

    stream = Stream(b"image")
    asset = sl.file(stream, filename="image.png")
    assert asset.read() == asset.read() == b"image"
    assert not stream.closed


def test_file_infers_open_file_name_and_never_closes_it(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(b"report")
    with path.open("rb") as stream:
        asset = sl.file(stream)
        assert asset.name == "report.txt"
        assert asset.read() == b"report"
        assert stream.tell() == 0
        assert not stream.closed


def test_bytes_need_a_name_and_metadata_can_be_overridden() -> None:
    with pytest.raises(ValueError, match="filename="):
        sl.file(b"content")
    asset = sl.file(b"content", filename="report.txt")
    assert isinstance(asset.source, InlineAsset)
    renamed = sl.file(asset, filename="report.bin", media_type="application/x-report")
    assert renamed.name == "report.bin"
    assert renamed.media_type == "application/x-report"
    assert renamed.source is asset.source
    assert sl.file(asset) is asset


def test_urls_and_paths_are_unambiguous() -> None:
    with pytest.raises(ValueError, match="use Path"):
        sl.file("report.txt")
    asset = sl.file("https://example.org/files/my%20image.png?token=abc")
    assert asset.name == "my image.png"
    assert asset.url == "https://example.org/files/my%20image.png?token=abc"
    assert asset.media_type == "image/png"


def test_media_metadata_belongs_to_a_single_entry() -> None:
    preview = sl.media(b"image", filename="image.png", description="A door", spoiler=True, key="preview")
    assert preview.items[0].description == "A door"
    assert preview.items[0].spoiler
    with pytest.raises(ValueError, match="one bare source"):
        sl.media(Path("one.png"), Path("two.png"), filename="image.png", key="gallery")
    omitted: None = None
    gallery = sl.media(sl.media_item(b"one", filename="one.png"), omitted, Path("two.png"), key="gallery")
    assert len(gallery.items) == 2


def test_repeating_existing_metadata_preserves_file_identity() -> None:
    asset = sl.file(b"image", filename="image.png")
    assert sl.file(asset, filename="image.png", media_type="image/png") is asset


def test_figure_does_not_silently_ignore_file_metadata() -> None:
    entry = sl.media_item(b"image", filename="image.png")
    with pytest.raises(ValueError, match="media_item"):
        sl.figure(entry, filename="other.png")
