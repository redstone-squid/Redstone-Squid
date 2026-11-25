"""Durable provenance across transaction, queue and worker boundaries."""

from functools import partial
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.actors import ActingContext, Actor
from squid.core.events.application import DomainEventService
from squid.core.events.dispatcher import DomainEventRunner
from squid.core.events.models import DomainEvent
from squid.core.persistence.provenance import WorkActivity
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork


class Fact:
    event_type: ClassVar[str] = "test.cause"
    schema_version: ClassVar[int] = 1
    aggregate_kind: ClassVar[str] = "test"
    aggregate_id: int = 42

    def payload(self) -> dict[str, object]:
        return {}


async def test_worker_effect_traces_to_impersonated_request_after_acknowledgement(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    context = ActingContext(Actor(account_id=12), Actor(account_id=34), session_id=uuid4())
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO domain_event_consumers (name) VALUES ('audit_test')"))

    with context.scope():
        async with factory() as uow:
            await uow.emit(Fact())
            await uow.commit()
        async with factory() as uow:
            await uow.emit(Fact())

    async def publish_followup(_event: DomainEvent) -> None:
        async with factory() as uow:
            await uow.emit(Fact())
            await uow.commit()

    runner = DomainEventRunner(
        DomainEventService(uow_factory=factory), "audit_test", {"test.cause": (publish_followup,)}
    )
    await runner.process_batch(context=ActingContext.worker("dispatcher"))

    async with migrated_session_factory() as session:
        (effect,) = (
            await session.scalars(
                select(WorkActivity).where(
                    WorkActivity.relation == "domain_events", WorkActivity.effective == "worker:events.audit_test"
                )
            )
        ).all()
        history = (
            (await session.execute(text("SELECT * FROM trace_work_activity(:id)"), {"id": effect.id})).mappings().all()
        )
        assert [row["transition"] for row in history] == ["insert", "claim", "insert"]
        assert history[0]["operator"] == "account:12"
        assert history[0]["effective"] == "account:34"
        assert history[0]["invocation_id"] == context.invocation_id
        assert history[0]["impersonation_session_id"] == context.session_id
        assert history[1]["operator"] == "worker:dispatcher"
        assert history[1]["acquired_claim"] == history[2]["parent_claim"]
        assert history[1]["invocation_id"] != effect.invocation_id
        assert history[2]["impersonation_session_id"] is None
        assert await session.scalar(text("SELECT count(*) FROM domain_events WHERE event_type = 'test.cause'")) == 2
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM domain_event_deliveries WHERE event_id = :event_id AND consumer = 'audit_test'"
                ),
                {"event_id": history[0]["item"]["event_id"]},
            )
            == 0
        )
        assert await session.scalar(text("SELECT nullif(current_setting('squid.work_attribution', true), '')")) is None
