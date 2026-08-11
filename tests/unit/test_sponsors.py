"""Safe public sponsor projection validation."""

from uuid import UUID

import pytest

from squid.sponsors import PublicSponsor

INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000801")


@pytest.mark.parametrize(
    "website_url",
    [
        "https://user:secret@example.test",
        "https://example.test/embedded path",
        "https://example.test/embedded\tcontrol",
        "https://example.test/embedded\ncontrol",
        "https://example.test:\u200b443",
        "https://example.test:99999",
        "javascript:alert(1)",
    ],
)
def test_public_sponsor_rejects_unsafe_website_urls(website_url: str) -> None:
    with pytest.raises(ValueError, match=r"Sponsor .*URL"):
        PublicSponsor(INSTALLATION_ID, website_url=website_url)


def test_public_sponsor_canonicalizes_a_valid_http_url() -> None:
    sponsor = PublicSponsor(INSTALLATION_ID, website_url="https://EXAMPLE.test")

    assert sponsor.website_url == "https://example.test/"
