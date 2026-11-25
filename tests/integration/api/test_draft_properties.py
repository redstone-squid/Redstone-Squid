"""Generated edits through the draft HTTP routes and PostgreSQL."""

from functools import partial
from uuid import uuid4

from httpx import AsyncClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.api.security import Caller, current_caller
from squid.api.v1.submissions import (
    authenticated_submission_caller,
    authenticated_submission_context,
    get_submission_drafts,
    get_submission_edits,
    get_submission_finalization,
    router,
)
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.edits.application import EditExecutor
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.submissions.application.drafts import SubmissionDraftService
from squid.core.submissions.application.edits import DRAFT_ANSWERS, DraftAnswersEdit
from squid.core.submissions.application.forms import CheckedInFormManifestRegistry
from tests.support.litestar import asgi_transport, route_app
from tests.support.submission_form import Finalization

edits = st.lists(
    st.tuples(
        st.sampled_from(["display_name", "description"]),
        st.text(st.characters(categories=("L", "N", "P", "S")), min_size=1, max_size=32),
    ),
    min_size=1,
    max_size=6,
)


# Examples share the migrated schema. Each owns its account, draft and edit history.
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(changes=edits)
@example(changes=[("display_name", "Door"), ("display_name", "Door")])
async def test_draft_edits_survive_readback(
    migrated_session_factory: async_sessionmaker[AsyncSession], changes: list[tuple[str, str]]
) -> None:
    async with migrated_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id

    uows = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    drafts = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=uows)
    executor = EditExecutor(uows, {DRAFT_ANSWERS: DraftAnswersEdit(drafts)})
    caller = Caller(kind="account", subject=f"account:{account_id}", account_id=account_id)
    app = route_app(
        [router],
        overrides={
            authenticated_submission_context: lambda: ActingContext.direct(UserActor(account_id=account_id)),
            authenticated_submission_caller: lambda: caller,
            current_caller: lambda: caller,
            get_submission_drafts: lambda: drafts,
            get_submission_edits: lambda: executor,
            get_submission_finalization: lambda: Finalization(),
        },
    )
    async with AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
        created = await client.post(
            "/submissions/drafts",
            json={"category": "other", "origin": "web", "client_capabilities": ["repeatable_text", "typed_credits"]},
        )
        assert created.status_code == 201, created.text
        path = f"/submissions/drafts/{created.json()['id']}"
        answers: dict[str, str] = {}
        revision = 0
        for index, (field, value) in enumerate(changes):
            body = {
                "base_revision": revision,
                "client_instance_id": "web:editor",
                "idempotency_key": f"draft-edit-{index}",
                "operations": [{"operation_id": str(uuid4()), "field_id": field, "kind": "set", "value": value}],
            }
            changed = await client.post(f"{path}/changes", json=body)
            assert changed.status_code == 200, changed.text
            retried = await client.post(f"{path}/changes", json=body)
            assert retried.status_code == 200, retried.text
            assert retried.json()["replayed"] is True

            if answers.get(field) != value:
                revision += 1
            answers[field] = value
            saved = await client.get(path)
            assert saved.status_code == 200, saved.text
            assert saved.json()["answers"] == answers
            assert saved.json()["revision"] == revision
