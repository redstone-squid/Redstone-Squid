"""Reconciliation job-draining tests.

What each resource renders is covered in `tests/unit/bot/posts/test_reconciler.py`;
this is only about claiming, acknowledging, and dead-lettering.
"""

import uuid
from unittest.mock import AsyncMock, Mock

from squid.bot.sync.reconciler import ReconciliationCog
from squid.sync import ReconciliationAction, ReconciliationJob, ReconciliationResource


def _job(
    *,
    action: ReconciliationAction = ReconciliationAction.REFRESH,
    generation: int = 3,
    resource_kind: ReconciliationResource = ReconciliationResource.BUILD,
) -> ReconciliationJob:
    return ReconciliationJob(
        id=1,
        resource_kind=resource_kind,
        source_key="42",
        action=action,
        generation=generation,
        attempts=0,
        claim_token=uuid.uuid4(),
    )


def _cog() -> tuple[ReconciliationCog, AsyncMock]:
    cog = ReconciliationCog.__new__(ReconciliationCog)
    bot = AsyncMock()
    bot.topic_bus.publish = Mock()
    cog.bot = bot
    return cog, bot


async def test_reconciler_starts_only_after_cog_registration_and_is_awaited_on_unload() -> None:
    task = Mock()
    bot = Mock()
    bot.background_tasks.start_periodic.return_value = task
    bot.background_tasks.cancel = AsyncMock()
    cog = ReconciliationCog(bot)

    bot.background_tasks.start_periodic.assert_not_called()
    await cog.cog_load()
    bot.background_tasks.start_periodic.assert_called_once_with(
        cog.process_reconciliation,
        name="discord-reconciliation",
        interval=15,
    )

    await cog.cog_unload()
    bot.background_tasks.cancel.assert_awaited_once_with(task)


async def test_a_claimed_job_is_rendered_then_acknowledged() -> None:
    cog, bot = _cog()

    await cog._process_job(_job(generation=7))

    bot.post_reconciler.reconcile.assert_awaited_once_with("build", "42", 7)
    bot.services.discord_reconciliation.complete.assert_awaited_once()
    bot.topic_bus.publish.assert_called_once_with(("build", "42"))


async def test_deletion_needs_no_branch_of_its_own() -> None:
    """A vanished resource wants no posts, so the diff loop removes what is left."""
    cog, bot = _cog()

    await cog._process_job(
        _job(
            action=ReconciliationAction.DELETE,
            generation=7,
            resource_kind=ReconciliationResource.VOTE_SESSION,
        )
    )

    bot.post_reconciler.reconcile.assert_awaited_once_with("vote_session", "42", 7)
    bot.services.discord_reconciliation.complete.assert_awaited_once()
    bot.topic_bus.publish.assert_called_once_with(("vote_session", "42"))


async def test_a_failed_render_is_retried_rather_than_acknowledged() -> None:
    cog, bot = _cog()
    bot.post_reconciler.reconcile.side_effect = RuntimeError("discord is down")
    bot.services.discord_reconciliation.fail.return_value = False

    await cog._process_job(_job())

    bot.services.discord_reconciliation.fail.assert_awaited_once()
    bot.services.discord_reconciliation.complete.assert_not_awaited()
    bot.topic_bus.publish.assert_not_called()
