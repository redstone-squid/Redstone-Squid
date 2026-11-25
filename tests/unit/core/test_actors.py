"""Identity requirements shared by application operations."""

from dataclasses import fields
from uuid import uuid4

import pytest

from squid.core.actors import (
    ActingContext,
    Actor,
    ActorKind,
    AnonymousActor,
    GuestActor,
    ServiceActor,
    UserActor,
    WorkerActor,
    impersonation_use,
)
from squid.core.errors import AuthenticationError, InvalidStateError


def test_audit_key_prefers_account_and_preserves_discord_fallback() -> None:
    assert UserActor(account_id=7, discord_user_id=42).key == "account:7"
    assert UserActor(discord_user_id=42).key == "discord:42"
    with pytest.raises(ValueError, match="identified actor"):
        _ = AnonymousActor().key


def test_operations_require_the_identity_they_persist() -> None:
    with pytest.raises(AuthenticationError):
        UserActor(discord_user_id=42).require_account_id()
    with pytest.raises(AuthenticationError):
        UserActor(account_id=7).require_discord_user_id()
    assert UserActor(account_id=7).require_account_id() == 7
    assert UserActor(discord_user_id=42).require_discord_user_id() == 42


def test_unresolved_rank_is_not_rank_zero() -> None:
    with pytest.raises(InvalidStateError, match="resolved role rank"):
        AnonymousActor().require_permission_rank()
    assert UserActor(account_id=7, highest_permission_rank=0).require_permission_rank() == 0


def test_worker_invocation_isolates_impersonation_and_restores_the_caller() -> None:
    audit_id = uuid4()
    user = ActingContext(UserActor(account_id=1), UserActor(account_id=2, audit_use_id=audit_id))
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
    assert GuestActor(guest_session_id=session).key == f"guest:{session}"
    assert ServiceActor(service_id="index").key == "service:index"
    assert AnonymousActor().kind is ActorKind.ANONYMOUS
    assert isinstance(WorkerActor(worker_name="index"), Actor)
    with pytest.raises(ValueError, match="account or Discord"):
        UserActor()


def test_verified_guild_membership_preserves_impersonation_and_credential_limits() -> None:
    operator = UserActor(account_id=11, credential_id="key:17", credential_scopes=frozenset({"vote.poll.cast"}))
    target = UserActor(
        account_id=22,
        credential_id=operator.credential_id,
        credential_scopes=operator.credential_scopes,
        audit_use_id=uuid4(),
    )
    context = ActingContext(operator, target, session_id=uuid4())
    resolved = context.with_actor(UserActor(account_id=22, discord_user_id=33, guild_id=44))

    assert resolved.operator is operator
    assert resolved.invocation_id == context.invocation_id
    assert resolved.session_id == context.session_id
    assert resolved.effective.audit_reference == target.audit_use_id
    assert resolved.effective.credential_reference == operator.credential_id
    assert resolved.effective.credential_limit == operator.credential_scopes
    with pytest.raises(InvalidStateError):
        context.with_actor(UserActor(account_id=11, guild_id=44))


def test_only_user_and_service_principals_can_carry_credentials() -> None:
    assert fields(AnonymousActor) == ()
    assert [item.name for item in fields(GuestActor)] == ["guest_session_id"]
    assert [item.name for item in fields(WorkerActor)] == ["worker_name"]
    assert {item.name for item in fields(ServiceActor)} == {"service_id", "credential_id", "credential_scopes"}
    with pytest.raises(ValueError, match="nonempty"):
        WorkerActor(worker_name="")
    with pytest.raises(ValueError, match="nonempty"):
        ServiceActor(service_id="")


def test_only_a_user_with_the_same_discord_identity_can_be_linked_to_an_account() -> None:
    invocation = ActingContext.direct(UserActor(discord_user_id=21))
    linked = invocation.with_actor(UserActor(account_id=1, discord_user_id=21))
    assert linked.effective.key == "account:1"
    assert linked.operator is linked.effective
    assert linked.invocation_id == invocation.invocation_id
    with pytest.raises(InvalidStateError):
        linked.with_actor(UserActor(account_id=2, discord_user_id=21))
    with pytest.raises(InvalidStateError):
        linked.with_actor(ServiceActor(service_id="account:1"))


def test_service_enrichment_preserves_the_original_credential_scope() -> None:
    limited = ServiceActor(service_id="indexer", credential_id="api-key:1", credential_scopes=frozenset({"build.read"}))
    context = ActingContext.direct(limited)
    assert context.with_actor(ServiceActor(service_id="indexer")).effective is limited
