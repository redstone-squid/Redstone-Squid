"""Review a showcase proposal through the HTTP routes and the persisted edit workflow."""

from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.api import security
from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.tags import router
from squid.core.accounts.domain import PrivacyNoticeReceipt
from squid.core.actors import ActingContext, UserActor
from squid.core.edits.application import EditExecutor
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.tags.application import TagService
from squid.core.tags.application.edits import (
    ALIAS_EDIT,
    MODERATION_EDIT,
    PROPOSAL_EDIT,
    TagAliasEdit,
    TagModerationEdit,
    TagProposalEdit,
)
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from tests.support.litestar import asgi_transport, route_app
from tests.unit.api.fakes import build_services


async def test_tag_review_and_restriction_alias(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    async with factory() as uow:
        account = await uow.accounts.create(consent=PrivacyNoticeReceipt.grant_current())
        await uow.commit()
    async with migrated_session_factory.begin() as session:
        restriction = TagDefinition(
            stable_key="official_hip",
            display_name="Hip",
            semantic_kind=TagSemanticKind.RESTRICTION,
            restriction_type="miscellaneous",
            moderation_status=TagModerationStatus.APPROVED,
            authority=TagAuthority.OFFICIAL,
            value_type=TagValueType.NONE,
        )
        session.add(restriction)
    permissions = PermissionService(uow_factory=factory)
    edits = EditExecutor(
        factory,
        {
            PROPOSAL_EDIT: TagProposalEdit(),
            MODERATION_EDIT: TagModerationEdit(permissions),
            ALIAS_EDIT: TagAliasEdit(permissions),
        },
    )
    services = replace(build_services(), tags=TagService(uow_factory=factory), edits=edits, permissions=permissions)
    app = route_app([router], overrides={})
    app.state.runtime = SimpleNamespace(services=services)
    context = ActingContext.direct(UserActor(account_id=account.id, is_bot_owner=True))
    caller = Caller(kind="account", subject="reviewer", account_id=account.id, nodes=UNBOUNDED, acting_context=context)
    monkeypatch.setattr(security, "authenticated_caller", AsyncMock(return_value=caller))
    with context.scope():
        async with httpx.AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
            first = await client.post("/tags/proposals", json={"name": "First"})
            second = await client.post("/tags/proposals", json={"name": "Second"})
            assert first.status_code == second.status_code == 201
            item, other = first.json(), second.json()
            queue = await client.get("/tags/proposals", params={"page_size": 1})
            assert queue.status_code == 200
            assert queue.json()["total"] == 2
            assert queue.json()["items"][0]["id"] == item["id"]
            base = f"/tags/proposals/{item['id']}"
            approved = await client.post(f"{base}/approve", json={"revision": item["revision"]})
            assert approved.status_code == 200
            stale = await client.post(f"{base}/archive", json={"revision": item["revision"]})
            assert stale.status_code == 412
            rejected = await client.post(f"/tags/proposals/{other['id']}/reject", json={"revision": other["revision"]})
            assert rejected.status_code == 200
            assert (await client.get("/tags/proposals", params={"status": "rejected"})).json()["items"][0][
                "id"
            ] == other["id"]
            assert (await client.get("/tags", params={"q": "First"})).json()["total"] == 1
            assert (
                await client.post(f"{base}/archive", json={"revision": approved.json()["revision"]})
            ).status_code == 200
            assert (await client.get("/tags", params={"q": "First"})).json()["total"] == 0
            alias = await client.post(
                f"/tags/{restriction.id}/aliases", json={"revision": restriction.revision, "alias": "Alternate"}
            )
            assert alias.status_code == 201
            result = (await client.get("/tags", params={"q": "alt"})).json()
            assert result["items"][0]["aliases"] == ["Alternate"]
