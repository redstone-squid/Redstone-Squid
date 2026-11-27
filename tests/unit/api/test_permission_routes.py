"""Permission administration over HTTP: subjects, explanations and conflicts."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, create_autospec
from uuid import uuid4

from litestar.testing import TestClient
from whenever import Instant

from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.permissions import create_grant
from squid.api.v1.schemas.permissions import AccountSubject, GrantRequest
from squid.core.permissions.application import PermissionAdministrationService
from squid.core.permissions.domain import Decision, Effect, Reason
from squid.core.permissions.domain.models import AccountGrantSubject, ExternalRoleGrantSubject
from squid.core.permissions.models import PermissionGrant
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app

INSPECTOR_CONFIG = TEST_CONFIG.model_copy(
    update={
        "api": TEST_CONFIG.api.model_copy(
            update={"secret_nodes": ["perm.node.view", "perm.subject.inspect", "perm.grant.guild"]}
        )
    }
)
AUTHORIZED = {"Authorization": TEST_SYNERGY_SECRET}


def client(admin: Any) -> Any:
    app, _database = build_app(permission_admin=admin, config=INSPECTOR_CONFIG)
    return TestClient(app, raise_server_exceptions=False)


def test_the_catalogue_is_grouped_by_domain() -> None:
    with client(create_autospec(PermissionAdministrationService, instance=True)) as http:
        response = http.get("/v1/permissions/nodes", headers=AUTHORIZED)

    assert response.status_code == 200
    domains = {domain["domain"]: domain["nodes"] for domain in response.json()}
    assert "version.entry.edit" in {node["name"] for node in domains["version"]}


def test_a_discord_role_is_explained_in_its_own_guild() -> None:
    admin = create_autospec(PermissionAdministrationService, instance=True)
    admin.explain.return_value = (Decision("build.submission.approve", allowed=False, reason=Reason.DEFAULT),)

    with client(admin) as http:
        response = http.get(
            "/v1/permissions/explain",
            params={"guild_id": "10", "discord_role_id": "20", "node": "build.submission.approve"},
            headers=AUTHORIZED,
        )

    assert response.status_code == 200
    assert response.json() == [{"node": "build.submission.approve", "allowed": False, "reason": "default", "trace": []}]
    (subject,) = admin.explain.call_args.args
    assert subject == ExternalRoleGrantSubject(guild_id=10, discord_role_id=20)
    assert admin.explain.call_args.kwargs["nodes"] == ("build.submission.approve",)


def test_explaining_needs_a_subject() -> None:
    with client(create_autospec(PermissionAdministrationService, instance=True)) as http:
        response = http.get("/v1/permissions/explain", params={"guild_id": "10"}, headers=AUTHORIZED)

    assert response.status_code == 400
    assert response.json()["context"]["fields"][0]["field"] == "/query/account_id"


async def test_a_grant_names_accounts_by_public_id_both_ways() -> None:
    public, granter = uuid4(), uuid4()
    stored = PermissionGrant(
        id=3,
        pattern="tag.**",
        effect=int(Effect.ALLOW),
        subject_account_id=7,
        granted_by_account_id=1,
        granted_at=Instant.from_utc(2026, 11, 27),
    )
    admin = create_autospec(PermissionAdministrationService, instance=True)
    admin.rules_for.return_value = (stored,)
    accounts = SimpleNamespace(
        resolve_public_id=AsyncMock(return_value=7), public_ids=AsyncMock(return_value={7: public, 1: granter})
    )
    caller = Caller(kind="account", subject="test", nodes=UNBOUNDED, account_id=1)
    request = GrantRequest.model_validate(
        {"subject": {"kind": "account", "account_id": str(public)}, "pattern": "tag.**"}
    )

    detail = await create_grant.fn(request, admin, cast(Any, SimpleNamespace(accounts=accounts)), caller)

    assert admin.grant.call_args.kwargs["subject"] == AccountGrantSubject(7)
    assert detail.subject == AccountSubject(account_id=public)
    assert detail.granted_by_account_id == granter
