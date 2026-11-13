"""Account errors identify the provider when a subject is supplied.

Internal account IDs and provider subjects belong in diagnostic context, not in
public error details.
"""

from uuid import UUID

import pytest

from squid.accounts.domain import IdentityProvider
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountNotFoundError,
    ConsentRequiredError,
    NoLinkedMinecraftAccountError,
)
from squid_ui.text import Text

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")


def test_a_discord_identity_names_its_own_provider_like_every_other() -> None:
    error = AccountNotFoundError(provider=IdentityProvider.DISCORD, subject="7")

    assert error.context["provider"] == IdentityProvider.DISCORD
    assert error.context["subject"] == "7"
    assert error.provider is IdentityProvider.DISCORD
    assert error.subject == "7"


def test_a_non_discord_identity_names_its_own_provider() -> None:
    error = AccountNotFoundError(provider=IdentityProvider.JAVA, subject=str(JAVA_UUID))

    assert error.context["provider"] == IdentityProvider.JAVA
    assert error.context["subject"] == str(JAVA_UUID)


def test_an_account_id_alone_carries_no_provider() -> None:
    error = AccountNotFoundError(42)

    assert error.context == {"account_id": 42}
    assert error.provider is None


def test_consent_errors_carry_both_the_account_and_the_identity() -> None:
    error = ConsentRequiredError(account_id=42, provider=IdentityProvider.JAVA, subject=str(JAVA_UUID))

    assert error.context == {"account_id": 42, "provider": IdentityProvider.JAVA, "subject": str(JAVA_UUID)}


def test_consent_errors_work_for_a_caller_known_only_by_its_account() -> None:
    error = ConsentRequiredError(account_id=42)

    assert error.context == {"account_id": 42}


def test_already_linked_names_the_conflicting_minecraft_account() -> None:
    error = AccountAlreadyLinkedError(provider=IdentityProvider.JAVA, subject=str(JAVA_UUID), minecraft_uuid=JAVA_UUID)

    assert error.context["minecraft_uuid"] == str(JAVA_UUID)
    assert error.context["provider"] == IdentityProvider.JAVA
    assert error.minecraft_uuid == JAVA_UUID
    assert isinstance(error.default_message, Text)
    assert "Discord" not in error.default_message.resolve()


def test_already_linked_requires_keywords() -> None:
    with pytest.raises(TypeError):
        AccountAlreadyLinkedError(7, JAVA_UUID)  # type: ignore[misc]


def test_no_linked_account_is_distinct_from_an_unknown_uuid() -> None:
    """One means "link something first", the other means "Mojang does not know that UUID"."""
    error = NoLinkedMinecraftAccountError(account_id=42)

    assert error.context == {"account_id": 42}
    assert isinstance(error.default_message, Text)
    assert "Minecraft account linked" in error.default_message.resolve()


def test_no_public_context_leaks_internal_identifiers() -> None:
    """Identity context is for logs. None of it may reach an unauthenticated reader."""
    errors = (
        AccountNotFoundError(provider=IdentityProvider.DISCORD, subject="7"),
        AccountNotFoundError(42),
        ConsentRequiredError(account_id=42),
        AccountAlreadyLinkedError(account_id=42, minecraft_uuid=JAVA_UUID),
        NoLinkedMinecraftAccountError(account_id=42),
    )
    for error in errors:
        assert not error.public_context, f"{type(error).__name__} exposed {error.public_context}"
