import socket
from pathlib import Path

import pytest

from squid.core.media.infrastructure.image_mirrors import fetch_image, public_socket


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1", "224.0.0.1", "::1"])
def test_socket_rejects_nonpublic_destination(address: str) -> None:
    with pytest.raises(ValueError, match="public IPv4"):
        public_socket((socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 80)))


@pytest.mark.parametrize("url", ["file:///tmp/content", "https://user@example.org/img", "http://example.org:123/img"])
async def test_fetch_rejects_invalid_urls_before_opening_destination(url: str, tmp_path: Path) -> None:
    path = tmp_path / "image"
    with pytest.raises(ValueError, match="Unsupported image URL"):
        await fetch_image(url, path)
    assert not path.exists()
