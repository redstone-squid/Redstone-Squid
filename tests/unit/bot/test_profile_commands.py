"""Profile rendering behavior."""

from dataclasses import replace
from uuid import UUID

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

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
DISCORD = replace(AccountIdentity.discord(7), id=1, avatar_key="abc")
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2)


def _fields_named(fields: list, name: str) -> str | None:
    return next((field.value for field in fields if field.name == name), None)


class TestOwnProfile:
    """The free-text half of your page. The linked accounts are the panel's
    (`test_account_panel.py`), which lists them one field each so it can offer their controls."""

    def test_links_render_as_markdown(self) -> None:
        profile = AccountProfile(account_id=1, links=(ProfileLink("Site", "https://example.com"),))

        fields = own_profile_fields(profile)

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

        credit = _fields_named(public_profile_fields(profile), "Creator credit")
        assert credit is not None
        assert "Notch" in credit
        assert "3" in credit

    def test_a_hidden_profile_still_renders_its_credits(self) -> None:
        profile = PublicCreatorProfile(
            public_id=UUID(int=1),
            hidden=True,
            aliases=(CreditedAlias("Notch", build_count=1),),
        )

        fields = public_profile_fields(profile)

        assert _fields_named(fields, "Creator credit") is not None
        assert _fields_named(fields, "Linked accounts") is None

    def test_public_identities_are_not_rendered_as_mentions(self) -> None:
        """A public page is read by strangers; a ping is not what a creator credit means."""
        profile = PublicCreatorProfile(
            public_id=UUID(int=1),
            hidden=False,
            identities=(PublicIdentity(IdentityProvider.DISCORD, "7", "squidder"),),
        )

        listed = _fields_named(public_profile_fields(profile), "Linked accounts")
        assert listed is not None
        assert "<@" not in listed
        assert "squidder" in listed
