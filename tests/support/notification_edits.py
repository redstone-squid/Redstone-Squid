"""Record notification transport commands; PostgreSQL tests cover revision enforcement."""

from typing import Protocol
from uuid import UUID

from squid.core.actors import Actor
from squid.edits.domain import EditCommand, EditOutcome, EditReceipt, EditResult, EditValues, Revision
from squid.notifications.domain import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.edits import (
    StatesValues,
    SubscriptionsValues,
    SubscriptionValues,
)
from squid.notifications.models import NotificationSubscription


class NotificationRecorder(Protocol):
    async def preferences(self, account_id: int) -> NotificationPreferences: ...
    async def set_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences: ...
    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription: ...
    async def mark_read(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> None: ...
    async def mark_unread(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> None: ...


class NotificationEditRecorder:
    def __init__(self, service: NotificationRecorder, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY) -> None:
        self.service = service
        self.visibility = visibility
        self.commands: list[EditCommand] = []

    async def execute(self, command: EditCommand, actor: Actor) -> EditResult:
        self.commands.append(command)
        account_id = actor.account_id
        assert account_id is not None
        values = command.values.unpack()
        after = command.values
        match command.kind:
            case "notifications.preferences":
                assert isinstance(values["web_enabled"], bool)
                assert isinstance(values["dm_enabled"], bool)
                old = await self.service.preferences(account_id)
                before = EditValues.of({"web_enabled": old.web_enabled, "dm_enabled": old.dm_enabled})
                await self.service.set_preferences(
                    account_id, web_enabled=values["web_enabled"], dm_enabled=values["dm_enabled"]
                )
            case "notifications.subscriptions":
                creation = values["create"]
                assert isinstance(creation, dict)
                desired = SubscriptionValues.model_validate(creation | {"id": 0, "enabled": True})
                row = await self.service.subscribe(
                    account_id, kind=desired.kind, subject_id=desired.subject_id, record_filter=desired.filter()
                )
                stored = SubscriptionValues.from_row(row)
                before = SubscriptionsValues(subscriptions=(stored.model_copy(update={"enabled": False}),)).values()
                after = SubscriptionsValues(subscriptions=(stored,)).values()
            case "notifications.read":
                states = StatesValues.model_validate(values).states
                before = EditValues.of({"states": {str(key): not read for key, read in states.items()}})
                for key, read in states.items():
                    method = self.service.mark_read if read else self.service.mark_unread
                    await method(account_id, key, visibility=self.visibility)
            case _:
                raise AssertionError(command.kind)
        revisions = tuple(
            Revision(version.target, (version.value if version.value is not None else 1) + 1)
            for version in command.expected
        )
        receipt = EditReceipt(command, before, after, revisions)
        return EditResult(
            EditOutcome(command.operation_id, actor.key, command.fingerprint, revisions, changed=True, final=False),
            receipt,
        )
