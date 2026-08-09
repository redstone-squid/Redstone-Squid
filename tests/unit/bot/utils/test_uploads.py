"""Public media upload validation tests."""

import pytest

from squid.bot.utils.uploads import MediaUploadError, validate_catbox_url


def test_catbox_url_validation_accepts_only_the_file_origin() -> None:
    assert validate_catbox_url(" https://files.catbox.moe/example.png\n") == "https://files.catbox.moe/example.png"


@pytest.mark.parametrize(
    "value",
    [
        "File upload failed",
        "http://files.catbox.moe/example.png",
        "https://catbox.moe/example.png",
        "https://files.catbox.moe@127.0.0.1/example.png",
        "https://files.catbox.moe:8443/example.png",
        "javascript:alert(1)",
    ],
)
def test_catbox_url_validation_rejects_errors_and_active_urls(value: str) -> None:
    with pytest.raises(MediaUploadError):
        validate_catbox_url(value)
