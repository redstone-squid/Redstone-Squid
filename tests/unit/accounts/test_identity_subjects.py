"""Per-provider subject validation, which is the domain's job and no longer the database's."""

from uuid import UUID

import pytest

from squid.accounts.domain import AccountIdentity, IdentityProvider

JAVA_UUID = UUID("069a79f4-44e9-4726-a5be-fca90e38aaf5")


@pytest.mark.parametrize(
    ("provider", "subject"),
    [
        (IdentityProvider.DISCORD, "123456789012345678"),
        (IdentityProvider.DISCORD, str(2**63 - 1)),
        (IdentityProvider.BEDROCK, "2535465049322445"),
        (IdentityProvider.BEDROCK, str(2**64 - 1)),
        (IdentityProvider.JAVA, str(JAVA_UUID)),
    ],
)
def test_canonical_subjects_are_accepted(provider: IdentityProvider, subject: str) -> None:
    assert AccountIdentity.for_provider(provider, subject).subject == subject


@pytest.mark.parametrize(
    ("provider", "subject"),
    [
        (IdentityProvider.DISCORD, "0"),
        (IdentityProvider.DISCORD, "-1"),
        (IdentityProvider.DISCORD, "007"),
        (IdentityProvider.DISCORD, str(2**63)),
        (IdentityProvider.DISCORD, "١٢٣"),
        (IdentityProvider.DISCORD, str(JAVA_UUID)),
        (IdentityProvider.BEDROCK, "0"),
        (IdentityProvider.BEDROCK, str(2**64)),
        (IdentityProvider.JAVA, "123"),
        (IdentityProvider.JAVA, ""),
    ],
)
def test_malformed_subjects_are_rejected(provider: IdentityProvider, subject: str) -> None:
    with pytest.raises(ValueError, match=r"subjects must be|XUIDs must be"):
        AccountIdentity.for_provider(provider, subject)


@pytest.mark.parametrize(
    "subject",
    [
        "069A79F4-44E9-4726-A5BE-FCA90E38AAF5",
        "069a79f444e94726a5befca90e38aaf5",
        "{069a79f4-44e9-4726-a5be-fca90e38aaf5}",
    ],
)
def test_java_subjects_normalize_to_canonical_form(subject: str) -> None:
    """Uppercase, unhyphenated, and braced UUIDs are the same identity, not three."""
    assert AccountIdentity.for_provider(IdentityProvider.JAVA, subject).subject == str(JAVA_UUID)


def test_every_provider_has_an_arm() -> None:
    """A new `IdentityProvider` member fails here as well as under basedpyright.

    The `match` in `for_provider` is exhaustive, so a missing arm falls through and
    returns `None` at runtime; this catches that without waiting for a type check.
    """
    samples = {
        IdentityProvider.DISCORD: "1",
        IdentityProvider.JAVA: str(JAVA_UUID),
        IdentityProvider.BEDROCK: "1",
    }
    for provider in IdentityProvider:
        assert provider in samples, f"{provider} has no sample subject; does it have a `for_provider` arm?"
        assert AccountIdentity.for_provider(provider, samples[provider]).provider is provider


def test_typed_conveniences_delegate_to_for_provider() -> None:
    assert AccountIdentity.discord(7) == AccountIdentity.for_provider(IdentityProvider.DISCORD, "7")
    assert AccountIdentity.bedrock(7, gamertag="Builder") == AccountIdentity.for_provider(
        IdentityProvider.BEDROCK, "7", display_name="Builder"
    )
    assert AccountIdentity.java(JAVA_UUID, username="Notch") == AccountIdentity.for_provider(
        IdentityProvider.JAVA, str(JAVA_UUID), display_name="Notch"
    )
