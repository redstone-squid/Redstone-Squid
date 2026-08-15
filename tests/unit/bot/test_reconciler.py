"""Desired Discord projection reconciler tests."""

from typing import Literal
from unittest.mock import AsyncMock, Mock

from whenever import Instant

from squid.bot.sync.reconciler import ReconciliationCog
from squid.messages.domain import MessageRecord
from squid.sync import SyncJob


def _job(
    *,
    action: Literal["refresh", "delete"] = "refresh",
    generation: int = 3,
    resource_kind: Literal["build", "vote_session"] = "build",
) -> SyncJob:
    return SyncJob(
        id=1,
        resource_kind=resource_kind,
        source_key="42",
        action=action,
        generation=generation,
        attempts=0,
        claimed_at=Instant.now(),
    )


def _cog(*, renders_posts: bool = True) -> tuple[ReconciliationCog, AsyncMock]:
    cog = ReconciliationCog.__new__(ReconciliationCog)
    bot = AsyncMock()
    bot.post_reconciler.handles = Mock(return_value=renders_posts)
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


async def test_a_rendered_resource_is_handed_to_the_post_reconciler() -> None:
    """Build posts are reconciled, not projected, so no message row is acknowledged."""
    cog, bot = _cog()

    await cog._process_job(_job(generation=7))

    bot.post_reconciler.reconcile.assert_awaited_once_with("build", "42", 7)
    bot.services.messages.mark_projection_applied.assert_not_awaited()
    bot.services.discord_sync.complete.assert_awaited_once()


async def test_a_delete_needs_no_special_case_for_a_rendered_resource() -> None:
    """A vanished resource wants no posts, and the diff loop removes what is left."""
    cog, bot = _cog()

    await cog._process_job(_job(action="delete", generation=7))

    bot.post_reconciler.reconcile.assert_awaited_once_with("build", "42", 7)
    bot.services.messages.untrack.assert_not_awaited()


async def test_vote_refresh_marks_only_the_rendered_generation_before_acknowledging() -> None:
    cog, bot = _cog(renders_posts=False)
    cog._refresh_vote = AsyncMock()  # type: ignore[method-assign]

    await cog._process_job(_job(generation=7, resource_kind="vote_session"))

    cog._refresh_vote.assert_awaited_once_with(42)
    bot.services.messages.mark_projection_applied.assert_awaited_once_with("vote_session", "42", 7)
    bot.services.discord_sync.complete.assert_awaited_once()


async def test_delete_removes_retained_discord_targets_and_tracking_rows() -> None:
    cog, bot = _cog(renders_posts=False)
    message = AsyncMock()
    bot.get_or_fetch_message.return_value = message
    bot.services.messages.list_projection.return_value = (
        MessageRecord(
            id=100,
            server_id=10,
            channel_id=20,
            author_id=30,
            purpose="vote",
            content=None,
            build_id=None,
            vote_session_id=None,
            updated_at=None,
            projection_resource_kind="vote_session",
            projection_source_key="42",
            desired_action="delete",
            desired_revision=4,
            applied_revision=3,
        ),
    )

    await cog._process_job(_job(action="delete", generation=4, resource_kind="vote_session"))

    message.delete.assert_awaited_once_with()
    bot.services.messages.untrack.assert_awaited_once_with(100)
    bot.services.messages.mark_projection_applied.assert_not_awaited()
    bot.services.discord_sync.complete.assert_awaited_once()
