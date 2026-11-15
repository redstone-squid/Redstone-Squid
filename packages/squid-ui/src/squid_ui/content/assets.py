"""File inputs shared by media previews and downloads.

Factories normalize inputs without reading them. Local files are snapshotted on first
use; reusing the resulting asset reuses those bytes. Discord delivery reads them in a
worker thread. Synchronous renderers and explicit file builders read synchronously.
"""

import hashlib
import mimetypes
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import BinaryIO
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True, slots=True)
class InlineAsset:
    """Transient bytes suitable only for a session mount."""

    data: bytes


@dataclass(frozen=True, slots=True)
class StoredAsset:
    """Host-resolved asset reference suitable for durable mounts."""

    reference: str


@dataclass(slots=True)
class LocalAsset:
    """A lazily snapshotted path or caller-owned binary stream.

    Streams are read from their position at first use and never closed. Seekable streams
    have their position restored. Keep streams open until first delivery; reuse the same
    asset for non-seekable streams, since creating another asset cannot replay them.
    """

    input: Path | BinaryIO
    _data: bytes | None = field(default=None, init=False, repr=False, compare=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    def read(self) -> bytes:
        """Return the cached snapshot, reading the input once if necessary."""
        with self._lock:
            if self._data is not None:
                return self._data
            if isinstance(self.input, Path):
                data = self.input.read_bytes()
            else:
                stream = self.input
                position = stream.tell() if stream.seekable() else None
                try:
                    data = stream.read()
                finally:
                    if position is not None:
                        stream.seek(position)
                if not isinstance(data, bytes):
                    msg = "file inputs must be binary streams, not text streams"
                    raise TypeError(msg)
            self._data = data
            return data


type AssetSource = InlineAsset | StoredAsset | LocalAsset


@dataclass(frozen=True, slots=True)
class Asset:
    """A normalized file, with metadata independent of its presentation.

    Most callers pass paths, bytes, or binary streams to media/download directly.
    Use `file()` to share one snapshot or supply metadata once; explicit Asset and
    StoredAsset values remain available for host-managed durable references.
    """

    key: str
    name: str
    media_type: str
    source: AssetSource

    def read(self) -> bytes:
        """Read local content. Stored references must be resolved by the host."""
        if isinstance(self.source, InlineAsset):
            return self.source.data
        if isinstance(self.source, LocalAsset):
            return self.source.read()
        msg = f"stored asset {self.key!r} needs a host resolver"
        raise TypeError(msg)

    @property
    def url(self) -> str | None:
        """The public HTTP(S) URL, if this file is already hosted."""
        if isinstance(self.source, StoredAsset):
            parsed = urlsplit(self.source.reference)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return self.source.reference
        return None


type FileInput = Asset | PathLike[str] | bytes | BinaryIO | str


def file(
    source: FileInput,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> Asset:
    """Describe a file without reading it, for use in media or downloads.

    Strings must be HTTP(S) URLs; use Path for filesystem paths. Bytes and unnamed
    streams require a filename. MIME type is inferred from the filename unless supplied.
    Paths and streams are snapshotted on first use; see LocalAsset for stream ownership.
    Share this result to reuse a snapshot across controls and repeated renders.
    """
    if isinstance(source, Asset):
        if filename is None and media_type is None:
            return source
        name = filename or source.name
        content = source.source
        identity = source.key
    elif isinstance(source, str):
        parsed = urlsplit(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            msg = "string file inputs must be HTTP(S) URLs; use Path for local files"
            raise ValueError(msg)
        name = filename or Path(unquote(parsed.path)).name or "download"
        content = StoredAsset(source)
        identity = source
    elif isinstance(source, PathLike):
        path = Path(source).absolute()
        name = filename or path.name
        content = LocalAsset(path)
        identity = str(path.absolute())
    elif isinstance(source, bytes):
        name = filename
        content = InlineAsset(source)
        identity = hashlib.sha256(source).hexdigest()
    elif callable(getattr(source, "read", None)):
        stream_name = getattr(source, "name", None)
        name = filename or (Path(stream_name).name if isinstance(stream_name, str) else None)
        content = LocalAsset(source)
        identity = f"stream:{id(source)}"
    else:
        msg = "expected a path, bytes, binary stream, URL, or Asset"
        raise TypeError(msg)
    if not name:
        msg = "bytes and unnamed streams require filename="
        raise ValueError(msg)
    if name in {".", ".."} or "/" in name or "\\" in name or any(ord(char) < 32 for char in name):
        msg = "filename must be a basename without control characters"
        raise ValueError(msg)
    mime = media_type or (source.media_type if isinstance(source, Asset) and filename is None else None)
    mime = mime or mimetypes.guess_file_type(name)[0] or "application/octet-stream"
    if isinstance(source, Asset) and name == source.name and mime == source.media_type:
        return source
    key = hashlib.sha256(f"{identity}\0{name}\0{mime}".encode()).hexdigest()
    return Asset(key, name, mime, content)
