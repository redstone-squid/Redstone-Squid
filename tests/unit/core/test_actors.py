"""Identity requirements shared by application operations."""

from uuid import uuid4

import pytest

from squid.core.actors import ActingContext, Actor, ActorKind, impersonation_use
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


def test_worker_invocation_isolates_impersonation_and_restores_the_caller() -> None:
    audit_id = uuid4()
    user = ActingContext(Actor(account_id=1), Actor(account_id=2, audit_use_id=audit_id))
    worker = ActingContext.worker("finalize")
    with user.scope():
        with worker.scope():
            assert ActingContext.current() is worker
            assert worker.effective.reference == "worker:finalize"
            assert impersonation_use.get() is None
        assert ActingContext.current() is user
        assert impersonation_use.get() == audit_id
    with pytest.raises(InvalidStateError):
        ActingContext.current()


def test_principals_have_distinct_namespaces() -> None:
    session = uuid4()
    assert Actor(guest_session_id=session).key == f"guest:{session}"
    assert Actor(service_id="index").key == "service:index"
    assert Actor().kind is ActorKind.ANONYMOUS
    with pytest.raises(ValueError, match="single principal"):
        Actor(account_id=1, worker_name="index")


def test_verified_guild_membership_preserves_impersonation_and_credential_limits() -> None:
    operator = Actor(account_id=11, credential_id="key:17", credential_scopes=frozenset({"vote.poll.cast"}))
    target = Actor(
        account_id=22,
        credential_id=operator.credential_id,
        credential_scopes=operator.credential_scopes,
        audit_use_id=uuid4(),
    )
    context = ActingContext(operator, target, session_id=uuid4())
    resolved = context.with_actor(Actor(account_id=22, discord_user_id=33, guild_id=44))

    assert resolved.operator is operator
    assert resolved.invocation_id == context.invocation_id
    assert resolved.session_id == context.session_id
    assert resolved.effective.audit_use_id == target.audit_use_id
    assert resolved.effective.credential_id == operator.credential_id
    assert resolved.effective.credential_scopes == operator.credential_scopes
    with pytest.raises(InvalidStateError):
        context.with_actor(Actor(account_id=11, guild_id=44))
