"""What a stranger sees: the visibility matrix applied by `present_public_profile`."""

from dataclasses import replace
from uuid import UUID

from whenever import Instant

from squid.accounts.domain import (
    AccountIdentity,
    AccountProfile,
    CreatorProfileRecord,
    CreditedAlias,
    IdentityProvider,
    ProfileLink,
    present_public_profile,
)

PUBLIC_ID = UUID("11111111-1111-4111-8111-111111111111")
JOINED = Instant.from_utc(2026, 1, 1)

DISCORD = AccountIdentity(
    provider=IdentityProvider.DISCORD, subject="123", display_name="squidder", id=1, avatar_key="abc"
)
JAVA = AccountIdentity(
    provider=IdentityProvider.JAVA,
    subject="0d1e5f2a-3b4c-4d5e-8f60-718293a4b5c6",
    display_name="Notch",
    id=2,
)


def record(profile: AccountProfile, *identities: AccountIdentity) -> CreatorProfileRecord:
    return CreatorProfileRecord(
        public_id=PUBLIC_ID,
        account_id=7,
        profile=profile,
        identities=identities or (DISCORD, JAVA),
        aliases=(CreditedAlias("Notch", build_count=3),),
        joined_at=JOINED,
    )


class TestVisibleProfile:
    def test_public_profile_shows_everything(self) -> None:
        profile = AccountProfile(
            account_id=7,
            display_name="Notch",
            bio="I build things",
            pronouns="they/them",
            links=(ProfileLink("Site", "https://example.com"),),
            avatar_identity_id=2,
        )
        public = present_public_profile(record(profile))
        assert not public.hidden
        assert public.display_name == "Notch"
        assert public.bio == "I build things"
        assert public.pronouns == "they/them"
        assert public.links == (ProfileLink("Site", "https://example.com"),)
        assert public.joined_at == JOINED
        assert {identity.provider for identity in public.identities} == {
            IdentityProvider.DISCORD,
            IdentityProvider.JAVA,
        }
        assert public.aliases == (CreditedAlias("Notch", build_count=3),)

    def test_public_identity_carries_no_verification_timestamp_or_id(self) -> None:
        public = present_public_profile(record(AccountProfile(account_id=7)))
        assert not any(hasattr(identity, "verified_at") for identity in public.identities)
        assert not any(hasattr(identity, "id") for identity in public.identities)


class TestHiddenIdentities:
    def test_hidden_identity_is_stripped(self) -> None:
        hidden_discord = replace(DISCORD, is_public=False)
        public = present_public_profile(record(AccountProfile(account_id=7), hidden_discord, JAVA))
        assert [identity.provider for identity in public.identities] == [IdentityProvider.JAVA]

    def test_avatar_does_not_leak_a_hidden_identity(self) -> None:
        hidden_java = replace(JAVA, is_public=False)
        profile = AccountProfile(account_id=7, avatar_identity_id=2)
        public = present_public_profile(record(profile, DISCORD, hidden_java))
        assert public.avatar_url is None

    def test_avatar_clears_when_the_source_identity_is_gone(self) -> None:
        profile = AccountProfile(account_id=7, avatar_identity_id=99)
        assert present_public_profile(record(profile)).avatar_url is None

    def test_java_avatar_derives_from_the_uuid(self) -> None:
        profile = AccountProfile(account_id=7, avatar_identity_id=2)
        public = present_public_profile(record(profile))
        assert public.avatar_url == f"https://mc-heads.net/avatar/{JAVA.subject}"

    def test_discord_avatar_needs_a_stored_key(self) -> None:
        profile = AccountProfile(account_id=7, avatar_identity_id=1)
        assert present_public_profile(record(profile)).avatar_url is not None
        keyless = replace(DISCORD, avatar_key=None)
        assert present_public_profile(record(profile, keyless, JAVA)).avatar_url is None


class TestHiddenProfile:
    def test_hidden_profile_degrades_to_aliases_and_credits(self) -> None:
        profile = AccountProfile(
            account_id=7,
            display_name="Notch",
            bio="secret",
            pronouns="they/them",
            links=(ProfileLink("Site", "https://example.com"),),
            avatar_identity_id=2,
            hidden=True,
        )
        public = present_public_profile(record(profile))
        assert public.hidden
        assert public.aliases == (CreditedAlias("Notch", build_count=3),)
        assert public.display_name is None
        assert public.bio is None
        assert public.pronouns is None
        assert public.links == ()
        assert public.avatar_url is None
        assert public.identities == ()
        assert public.joined_at is None

    def test_hidden_profile_keeps_its_public_id_so_builds_still_resolve(self) -> None:
        public = present_public_profile(record(AccountProfile(account_id=7, hidden=True)))
        assert public.public_id == PUBLIC_ID


class TestRedirects:
    def test_merged_creator_reports_its_canonical_id(self) -> None:
        canonical = UUID("22222222-2222-4222-8222-222222222222")
        base = record(AccountProfile(account_id=7))
        public = present_public_profile(replace(base, canonical_public_id=canonical))
        assert public.was_redirected
        assert public.canonical_public_id == canonical

    def test_unmerged_creator_is_not_redirected(self) -> None:
        assert not present_public_profile(record(AccountProfile(account_id=7))).was_redirected
