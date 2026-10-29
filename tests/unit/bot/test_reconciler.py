"""Reconciliation batches acknowledge only rendered and published resources."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import anyio
import pytest

from squid.bot.app import RedstoneSquid
from squid.bot.sync.reconciler import ReconciliationCog
from squid.posts.domain import ResourceKind
from squid.runtime import BackgroundTaskSupervisor
from squid.sync import DiscordReconciliationService, ReconciliationAction, ReconciliationJob, ReconciliationResource
from squid.topics import resource_topic
from squid_reactivity import LocalTopicBus


def _job(
    resource: ReconciliationResource = ReconciliationResource.BUILD,
    *,
    action: ReconciliationAction = ReconciliationAction.REFRESH,
) -> ReconciliationJob:
    return ReconciliationJob(1, resource, "42", action, 7, 0, uuid.uuid4())


class QueueRecorder:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.pending: list[ReconciliationJob] = []
        self.completed = anyio.Event()
        self.claims = 0

    async def claim(self, *, limit: int) -> Sequence[ReconciliationJob]:
        self.claims += 1
        jobs, self.pending = self.pending[:limit], self.pending[limit:]
        return jobs

    async def complete(self, job: ReconciliationJob) -> bool:
        self.events.append(("complete", job))
        self.completed.set()
        return True

    async def fail(self, job: ReconciliationJob, error: str, *, max_attempts: int) -> bool:
        self.events.append(("fail", job, error))
        return job.attempts + 1 >= max_attempts


class Renderer:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.failures: set[ResourceKind] = set()

    async def reconcile(self, resource_kind: ResourceKind, source_key: str, generation: int) -> None:
        self.events.append(("render", resource_kind, source_key, generation))
        if resource_kind in self.failures:
            raise RuntimeError("discord is down")


@dataclass(frozen=True)
class Services:
    discord_reconciliation: DiscordReconciliationService


class ReconciliationHarness:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.queue = QueueRecorder(self.events)
        self.post_reconciler = Renderer(self.events)
        self.services = Services(DiscordReconciliationService(self.queue))
        self.background_tasks = BackgroundTaskSupervisor()
        self.topic_publisher = LocalTopicBus()
        self.ready = anyio.Event()
        self.waiting_for_ready = anyio.Event()
        self.cog = ReconciliationCog(cast(RedstoneSquid, self))

    async def wait_until_ready(self) -> None:
        self.waiting_for_ready.set()
        await self.ready.wait()

    def enqueue(self, *jobs: ReconciliationJob) -> None:
        self.queue.pending.extend(jobs)
        for job in jobs:
            self.topic_publisher.subscribe(
                resource_topic(job.resource_kind.post_kind, job.source_key),
                lambda topic: self.events.append(("publish", topic)),
            )


@pytest.fixture
def reconciler() -> ReconciliationHarness:
    return ReconciliationHarness()


async def test_registration_waits_for_readiness_and_unload_settles_the_job(reconciler: ReconciliationHarness) -> None:
    job = _job()
    reconciler.enqueue(job)
    async with reconciler.background_tasks.running():
        assert reconciler.cog._task is None
        await reconciler.cog.cog_load()
        with anyio.fail_after(1):
            await reconciler.waiting_for_ready.wait()
            assert reconciler.queue.claims == 0
            reconciler.ready.set()
            await reconciler.queue.completed.wait()
            await reconciler.cog.cog_unload()

        assert reconciler.cog._task is not None
        assert reconciler.cog._task.finished.is_set()
        assert reconciler.queue.claims == 1


async def test_batch_renders_and_publishes_before_acknowledging(reconciler: ReconciliationHarness) -> None:
    refresh = _job()
    deletion = _job(ReconciliationResource.VOTE_SESSION, action=ReconciliationAction.DELETE)
    draft = _job(ReconciliationResource.SUBMISSION_DRAFT)
    reconciler.enqueue(refresh, deletion, draft)
    reconciler.ready.set()

    await reconciler.cog.process_reconciliation()

    assert reconciler.events == [
        ("render", "build", "42", 7),
        ("publish", resource_topic("build", "42")),
        ("complete", refresh),
        ("render", "vote_session", "42", 7),
        ("publish", resource_topic("vote_session", "42")),
        ("complete", deletion),
        ("publish", resource_topic("submission_draft", "42")),
        ("complete", draft),
    ]


async def test_failed_render_retries_without_publishing_and_batch_continues(reconciler: ReconciliationHarness) -> None:
    failed = _job()
    following = _job(ReconciliationResource.VOTE_SESSION)
    reconciler.enqueue(failed, following)
    reconciler.post_reconciler.failures.add("build")
    reconciler.ready.set()

    await reconciler.cog.process_reconciliation()

    assert reconciler.events == [
        ("render", "build", "42", 7),
        ("fail", failed, "discord is down"),
        ("render", "vote_session", "42", 7),
        ("publish", resource_topic("vote_session", "42")),
        ("complete", following),
    ]
