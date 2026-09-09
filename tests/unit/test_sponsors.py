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


def test_public_sponsor_reports_interior_whitespace_as_such() -> None:
    """The length message named the wrong failure for a URL well under its limit."""
    with pytest.raises(ValueError, match="must not contain whitespace or control characters"):
        PublicSponsor(INSTALLATION_ID, website_url="https://example.test/a b")


def test_public_sponsor_trims_padded_text_fields() -> None:
    """PublicServerProfile accepts padding, so rejecting it here would drop the attribution."""
    sponsor = PublicSponsor(
        INSTALLATION_ID,
        display_name="  Example Server  ",
        address="\texample.test\n",
        description=" A redstone server. ",
        website_url="  https://example.test  ",
    )

    assert sponsor.display_name == "Example Server"
    assert sponsor.address == "example.test"
    assert sponsor.description == "A redstone server."
    assert sponsor.website_url == "https://example.test/"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_public_sponsor_still_rejects_blank_text_fields(blank: str) -> None:
    with pytest.raises(ValueError, match=r"Sponsor display name must contain 1 to 80 characters\."):
        PublicSponsor(INSTALLATION_ID, display_name=blank)


def test_public_sponsor_measures_length_after_trimming() -> None:
    sponsor = PublicSponsor(INSTALLATION_ID, display_name=f"  {'a' * 80}  ")

    assert sponsor.display_name == "a" * 80

    with pytest.raises(ValueError, match=r"Sponsor display name must contain 1 to 80 characters\."):
        PublicSponsor(INSTALLATION_ID, display_name="a" * 81)
