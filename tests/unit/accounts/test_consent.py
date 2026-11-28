from squid.core.accounts.domain import PRIVACY_NOTICE, PRIVACY_NOTICE_VERSION


def test_the_notice_names_everything_it_is_consent_to() -> None:
    """Check the notice covers each data use while allowing its surrounding wording to change."""
    notice = PRIVACY_NOTICE.resolve().casefold()

    assert "discord user id" in notice  # what is stored
    assert "minecraft uuid" in notice
    assert "build credit" in notice  # what is claimed
    assert "staff review requests" in notice  # and what is not taken from others
    assert "submitting a build publishes it" in notice  # what submitting does
    assert "public catalogue" in notice
    assert "licence" in notice  # deferred to the per-submission choice, but named
    assert "creator page is public" in notice  # what is published about you
    assert "notifications are covered by this notice" in notice
    assert "off by default" in notice


def test_the_notice_is_plain_text_in_paragraphs() -> None:
    """It is rendered into a Discord card, an HTML page and a terminal alike."""
    notice = PRIVACY_NOTICE.resolve()
    assert "\n\n" in notice
    assert "<" not in notice


def test_the_version_is_an_iso_date() -> None:
    year, month, day = PRIVACY_NOTICE_VERSION.split("-")
    assert (len(year), len(month), len(day)) == (4, 2, 2)
    assert PRIVACY_NOTICE_VERSION.replace("-", "").isdigit()
