"""Profile rendering and the modal's link parsing."""

from dataclasses import replace
from uuid import UUID

import pytest

from squid.accounts.domain import (
    AccountIdentity,
    AccountProfile,
    CreditedAlias,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
    PublicIdentity,
)
from squid.bot.profile_render import own_profile_avatar, own_profile_fields, public_profile_fields
from squid.bot.verify import _parse_link_lines
from squid.core.errors import ValidationError

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
LOCALE = "en"
DISCORD = replace(AccountIdentity.discord(7), id=1, avatar_key="abc")
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2)


def _fields_named(fields: list, name: str) -> str | None:
    return next((field.value for field in fields if field.name == name), None)


class TestOwnProfile:
    def test_hidden_identities_are_listed_and_marked(self) -> None:
        """The self view must show what is hidden: you can only unhide what you can see."""
        identities = (DISCORD, replace(JAVA, is_public=False))
        fields = own_profile_fields(AccountProfile(account_id=1), identities, LOCALE)

        listed = _fields_named(fields, "Linked accounts")
        assert listed is not None
        assert "Notch" in listed
        assert "(hidden)" in listed

    def test_a_discord_identity_renders_as_a_mention(self) -> None:
        fields = own_profile_fields(AccountProfile(account_id=1), (DISCORD,), LOCALE)

        listed = _fields_named(fields, "Linked accounts")
        assert listed is not None
        assert "<@7>" in listed

    def test_links_render_as_markdown(self) -> None:
        profile = AccountProfile(account_id=1, links=(ProfileLink("Site", "https://example.com"),))

        fields = own_profile_fields(profile, (), LOCALE)

        assert _fields_named(fields, "Links") == "[Site](https://example.com)"

    def test_your_own_avatar_shows_even_when_its_identity_is_hidden(self) -> None:
        """Hiding an identity from strangers is not a reason to hide your avatar from yourself."""
        profile = AccountProfile(account_id=1, avatar_identity_id=2)

        assert own_profile_avatar(profile, (replace(JAVA, is_public=False),)) == (
            f"https://mc-heads.net/avatar/{JAVA_UUID}",
        )

    def test_no_avatar_when_the_source_identity_is_gone(self) -> None:
        assert own_profile_avatar(AccountProfile(account_id=1, avatar_identity_id=99), (JAVA,)) == ()


class TestPublicProfile:
    def test_credits_report_their_build_counts(self) -> None:
        profile = PublicCreatorProfile(
            public_id=UUID(int=1),
            hidden=False,
            aliases=(CreditedAlias("Notch", build_count=3),),
        )

        credit = _fields_named(public_profile_fields(profile, LOCALE), "Creator credit")
        assert credit is not None
        assert "Notch" in credit
        assert "3" in credit

    def test_a_hidden_profile_still_renders_its_credits(self) -> None:
        profile = PublicCreatorProfile(
            public_id=UUID(int=1),
            hidden=True,
            aliases=(CreditedAlias("Notch", build_count=1),),
        )

        fields = public_profile_fields(profile, LOCALE)

        assert _fields_named(fields, "Creator credit") is not None
        assert _fields_named(fields, "Linked accounts") is None

    def test_public_identities_are_not_rendered_as_mentions(self) -> None:
        """A public page is read by strangers; a ping is not what a creator credit means."""
        profile = PublicCreatorProfile(
            public_id=UUID(int=1),
            hidden=False,
            identities=(PublicIdentity(IdentityProvider.DISCORD, "7", "squidder"),),
        )

        listed = _fields_named(public_profile_fields(profile, LOCALE), "Linked accounts")
        assert listed is not None
        assert "<@" not in listed
        assert "squidder" in listed


class TestLinkLineParsing:
    def test_lines_split_on_the_first_pipe(self) -> None:
        parsed = _parse_link_lines("Site | https://example.com\nVideos | https://youtube.com/@x", LOCALE)

        assert parsed == (
            ProfileLink("Site", "https://example.com"),
            ProfileLink("Videos", "https://youtube.com/@x"),
        )

    def test_blank_lines_are_ignored(self) -> None:
        assert _parse_link_lines("\n\nSite | https://example.com\n\n", LOCALE) == (
            ProfileLink("Site", "https://example.com"),
        )

    def test_a_line_without_a_separator_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _parse_link_lines("https://example.com", LOCALE)

    def test_parsing_does_not_second_guess_the_domain_validator(self) -> None:
        """Splitting is all this does; the service decides whether the URL is acceptable."""
        assert _parse_link_lines("Bad | http://example.com", LOCALE) == (ProfileLink("Bad", "http://example.com"),)
