"""Follow the creator or record currently being viewed."""

from functools import partial
from typing import TYPE_CHECKING, cast
from uuid import UUID

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import with_consented_account
from squid.bot.ui import tr
from squid.core.actors import ActingContext
from squid.core.edits.domain import EditCommand, EditValues, Revision
from squid.core.notifications.domain import SubscriptionKind
from squid.core.notifications.edits import SUBSCRIPTIONS_EDIT, notification_target

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


def follow_control(kind: SubscriptionKind, subject_id: UUID) -> sl.semantic.ActionControl[sd.Request]:
    return sl.action_control(
        tr(t"context-follow-creator = Follow creator")
        if kind is SubscriptionKind.CREATOR
        else tr(t"context-follow-record = Follow record"),
        partial(_follow, kind, subject_id),
        key="follow",
    )


async def _follow(kind: SubscriptionKind, subject_id: UUID, event: sd.PressEvent) -> None:
    bot = cast("RedstoneSquid", event.context.client)

    async def save(source: sd.ActionEvent, account_id: int) -> None:
        _existing, revision = await bot.services.notifications.subscription_snapshot(
            account_id,
            kind=kind,
            subject_id=subject_id,
            record_filter=None,
        )
        actor = await bot.actors.resolve(source.context, refresh=True)
        await bot.services.edits.execute(
            EditCommand(
                SUBSCRIPTIONS_EDIT,
                (Revision(notification_target(account_id), revision),),
                EditValues.of({"create": {"kind": kind.value, "subject_id": str(subject_id)}}),
            ),
            context=ActingContext.for_actor(actor),
        )
        await source.notice(tr(t"context-follow-saved = Following. Manage your follows in /notifications."))

    await with_consented_account(event, bot.services.accounts, save)
