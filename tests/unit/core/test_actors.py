"""Identity requirements shared by application operations."""

import pytest

from squid.core.actors import Actor
from squid.core.errors import AuthenticationError, InvalidStateError


def test_audit_key_prefers_account_and_preserves_discord_fallback() -> None:
    assert Actor(account_id=7, discord_user_id=42).key == "account:7"
    assert Actor(discord_user_id=42).key == "discord:42"
    with pytest.raises(ValueError, match="identified actor"):
        _ = Actor().key


def test_operations_require_the_identity_they_persist() -> None:
    with pytest.raises(AuthenticationError):
        Actor(discord_user_id=42).require_account_id()
    with pytest.raises(AuthenticationError):
        Actor(account_id=7).require_discord_user_id()
    assert Actor(account_id=7).require_account_id() == 7
    assert Actor(discord_user_id=42).require_discord_user_id() == 42


def test_unresolved_rank_is_not_rank_zero() -> None:
    with pytest.raises(InvalidStateError, match="resolved role rank"):
        Actor().require_permission_rank()
    assert Actor(highest_permission_rank=0).require_permission_rank() == 0
