"""Shared edits for channel preferences, stable follows, and per-item read state."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, StrictBool
from whenever import Instant

from squid.accounts.errors import ConsentRequiredError
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, DataIntegrityError, ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.edits.fields import Aggregate, FieldCommand
from squid.notifications.application import NotificationRepository, NotificationUnitOfWork
from squid.notifications.domain import InboxVisibility, RecordSubscriptionFilter, SubscriptionKind
from squid.notifications.errors import NotificationNotFoundError, NotificationSubscriptionNotFoundError
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import ACCOUNT_SELF_MANAGE, BUILD_SUBMISSION_VIEW_PENDING
from squid.persistence.revisions import require_revision

PREFERENCES_EDIT = "notifications.preferences"
SUBSCRIPTIONS_EDIT = "notifications.subscriptions"
INBOX_EDIT = "notifications.read"


def notification_target(account_id: int) -> EditTarget:
    return EditTarget("notification-settings", str(account_id))


def inbox_target(notification_id: int) -> EditTarget:
    return EditTarget("inbox", str(notification_id))


class NotificationEditRepository(NotificationRepository, Protocol):
    async def lock_settings(self, account_id: int) -> int: ...

    async def advance_settings(self, account_id: int) -> int: ...

    async def subscription(self, account_id: int, subscription_id: int) -> NotificationSubscription | None: ...

    async def reserve_subscription_id(self) -> int: ...

    async def save_subscription(self, row: NotificationSubscription) -> None: ...

    async def lock_inbox(
        self, account_id: int, ids: tuple[int, ...], *, visibility: InboxVisibility
    ) -> Sequence[InboxNotification]: ...

    async def flush_inbox(self) -> None: ...


class NotificationEditUnitOfWork(NotificationUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    @property
    def notifications(self) -> NotificationEditRepository: ...


async def authorize(
    uow: NotificationEditUnitOfWork, permissions: PermissionService, actor: Actor, account_id: int
) -> None:
    if actor.account_id != account_id or not await permissions.allows_in(uow, actor, ACCOUNT_SELF_MANAGE):
        raise AuthorizationError
    if not (await uow.notifications.get_preferences(account_id)).has_current_consent:
        raise ConsentRequiredError(account_id=account_id)


class NotificationSettingsEdit:
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    @staticmethod
    def account_id(command: EditCommand) -> int:
        if len(command.expected) != 1 or command.expected[0].target.kind != "notification-settings":
            message = "Notification settings edits must guard the account's notification revision"
            raise ValueError(message)
        return int(command.expected[0].target.key)

    async def lock(self, uow: NotificationEditUnitOfWork, command: EditCommand) -> Revision:
        account_id = self.account_id(command)
        return require_revision(command.expected[0], await uow.notifications.lock_settings(account_id))


class PreferencesValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    web_enabled: bool
    dm_enabled: bool


@dataclass(slots=True)
class NotificationSettingsState:
    account_id: int
    revision: int


class NotificationPreferencesEdit(
    FieldCommand[NotificationEditUnitOfWork, NotificationSettingsState, PreferencesValues]
):
    def __init__(self, permissions: PermissionService) -> None:
        async def load(uow: NotificationEditUnitOfWork, command: EditCommand) -> NotificationSettingsState:
            account_id = int(command.expected[0].target.key)
            return NotificationSettingsState(account_id, await uow.notifications.lock_settings(account_id))

        async def flush(
            uow: NotificationEditUnitOfWork, root: NotificationSettingsState, expected: Revision
        ) -> Revision:
            return Revision(expected.target, root.revision)

        async def authorize_root(
            uow: NotificationEditUnitOfWork, actor: Actor, root: NotificationSettingsState
        ) -> None:
            await authorize(uow, permissions, actor, root.account_id)

        super().__init__(
            Aggregate("notification-settings", load, lambda root: root.revision, flush),
            PreferencesValues,
            authorize_root,
        )

    async def capture(
        self, uow: NotificationEditUnitOfWork, root: NotificationSettingsState, command: EditCommand
    ) -> PreferencesValues:
        return PreferencesValues.model_validate(
            await uow.notifications.get_preferences(root.account_id), from_attributes=True
        )

    async def write(
        self,
        uow: NotificationEditUnitOfWork,
        root: NotificationSettingsState,
        before: PreferencesValues,
        after: PreferencesValues,
    ) -> None:
        result = await uow.notifications.update_preferences(
            root.account_id, web_enabled=after.web_enabled, dm_enabled=after.dm_enabled
        )
        assert result is not None
        root.revision = result.revision


class SubscriptionValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    kind: SubscriptionKind
    subject_id: UUID | None = None
    record_filter: dict[str, JsonValue] | None = None
    enabled: bool

    @classmethod
    def from_row(cls, row: NotificationSubscription) -> SubscriptionValues:
        return cls(
            id=row.id,
            kind=row.kind,
            subject_id=row.subject_id,
            record_filter=None
            if row.record_filter is None
            else cast(dict[str, JsonValue], row.record_filter.as_dict()),
            enabled=row.enabled,
        )

    def filter(self) -> RecordSubscriptionFilter | None:
        if self.record_filter is None:
            return None
        try:
            return RecordSubscriptionFilter.from_dict(dict(self.record_filter))
        except DataIntegrityError as error:
            raise ValidationError(str(error)) from error

    async def validate_target(self, repository: NotificationEditRepository) -> None:
        if self.kind is SubscriptionKind.RECORD_FILTER:
            if self.subject_id is not None or self.record_filter is None:
                message = "A record-filter follow requires only a structured filter."
                raise ValidationError(message)
            self.filter()
        elif self.subject_id is None or self.record_filter is not None:
            message = "A creator or record follow requires only a subject ID."
            raise ValidationError(message)
        elif self.enabled and not await repository.subscription_target_exists(self.kind, self.subject_id):
            raise NotificationSubscriptionNotFoundError


class SubscriptionsValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriptions: tuple[SubscriptionValues, ...]

    def values(self) -> EditValues:
        return EditValues.of(self.model_dump(mode="json"))


class StatesValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    states: dict[int, StrictBool]


class NotificationSubscriptionsEdit(NotificationSettingsEdit):
    async def prepare(self, uow: NotificationEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        revision = await self.lock(uow, command)
        account_id = self.account_id(command)
        await authorize(uow, self._permissions, actor, account_id)
        values = command.values.unpack()
        before: list[SubscriptionValues] = []
        after: list[SubscriptionValues] = []
        if set(values) == {"create"}:
            creation = values["create"]
            if not isinstance(creation, dict):
                message = "A follow requires a subject or filter."
                raise ValidationError(message)
            desired = SubscriptionValues.model_validate(creation | {"id": 0, "enabled": True})
            await desired.validate_target(uow.notifications)
            record_filter = desired.filter()
            if record_filter is not None:
                desired = desired.model_copy(update={"record_filter": record_filter.as_dict()})
            existing = await uow.notifications.matching_subscription(
                account_id, kind=desired.kind, subject_id=desired.subject_id, record_filter=desired.filter()
            )
            if existing is None:
                desired = desired.model_copy(update={"id": await uow.notifications.reserve_subscription_id()})
                previous = desired.model_copy(update={"enabled": False})
            else:
                previous = SubscriptionValues.from_row(existing)
                desired = previous.model_copy(update={"enabled": True})
            before.append(previous)
            after.append(desired)
        else:
            if set(values) == {"states"}:
                requested = StatesValues.model_validate(values).states
                desired_rows = []
                for subscription_id, enabled in sorted(requested.items()):
                    row = await uow.notifications.subscription(account_id, subscription_id)
                    if row is None:
                        raise NotificationSubscriptionNotFoundError
                    desired_rows.append(SubscriptionValues.from_row(row).model_copy(update={"enabled": enabled}))
            else:
                desired_rows = list(SubscriptionsValues.model_validate(values).subscriptions)
            if len({row.id for row in desired_rows}) != len(desired_rows):
                message = "A follow may occur only once in an edit."
                raise ValidationError(message)
            for desired in sorted(desired_rows, key=lambda row: row.id):
                current = await uow.notifications.subscription(account_id, desired.id)
                if current is None:
                    raise NotificationSubscriptionNotFoundError
                previous = SubscriptionValues.from_row(current)
                if previous.model_copy(update={"enabled": desired.enabled}) != desired:
                    message = "A follow edit can only change whether it is enabled."
                    raise ValidationError(message)
                await desired.validate_target(uow.notifications)
                before.append(previous)
                after.append(desired)
        active = {
            row.id: SubscriptionValues.from_row(row) for row in await uow.notifications.list_subscriptions(account_id)
        }
        for row in after:
            if row.enabled:
                active[row.id] = row
            else:
                active.pop(row.id, None)
        signatures = [(row.kind, row.subject_id, row.filter()) for row in active.values()]
        if len(set(signatures)) != len(signatures):
            message = "An equivalent follow is already enabled."
            raise ValidationError(message)
        return PreparedEdit(
            SubscriptionsValues(subscriptions=tuple(before)).values(),
            SubscriptionsValues(subscriptions=tuple(after)).values(),
            revisions=(revision,),
        )

    async def apply(
        self, uow: NotificationEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        account_id = self.account_id(command)
        for desired in sorted(
            SubscriptionsValues.model_validate(prepared.after.unpack()).subscriptions, key=lambda row: row.enabled
        ):
            row = await uow.notifications.subscription(account_id, desired.id)
            if row is None:
                row = NotificationSubscription(
                    id=desired.id,
                    account_id=account_id,
                    kind=desired.kind,
                    subject_id=desired.subject_id,
                    record_filter=desired.filter(),
                    enabled=desired.enabled,
                )
            else:
                row.enabled = desired.enabled
            await uow.notifications.save_subscription(row)
        revision = await uow.notifications.advance_settings(account_id)
        return (Revision(notification_target(account_id), revision),)


class InboxReadEdit:
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    async def rows(
        self, uow: NotificationEditUnitOfWork, actor: Actor, command: EditCommand
    ) -> Sequence[InboxNotification]:
        account_id = actor.account_id
        if account_id is None:
            raise AuthorizationError
        await uow.notifications.lock_settings(account_id)
        await authorize(uow, self._permissions, actor, account_id)
        values = StatesValues.model_validate(command.values.unpack())
        if {inbox_target(key) for key in values.states} != {version.target for version in command.expected}:
            message = "Read-state edits must guard exactly the selected inbox items."
            raise ValidationError(message)
        visibility = InboxVisibility(
            include_staff=await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_VIEW_PENDING)
        )
        rows = await uow.notifications.lock_inbox(account_id, tuple(sorted(values.states)), visibility=visibility)
        if {row.id for row in rows} != values.states.keys():
            raise NotificationNotFoundError
        versions = {version.target: version for version in command.expected}
        for row in rows:
            require_revision(versions[inbox_target(row.id)], row.revision)
        return rows

    async def prepare(self, uow: NotificationEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedEdit:
        rows = await self.rows(uow, actor, command)
        before = StatesValues(states={row.id: row.read_at is not None for row in rows})
        after = StatesValues.model_validate(command.values.unpack())
        return PreparedEdit(
            EditValues.of(before.model_dump(mode="json")),
            EditValues.of(after.model_dump(mode="json")),
            revisions=tuple(Revision(inbox_target(row.id), row.revision) for row in rows),
        )

    async def apply(
        self, uow: NotificationEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        rows = await self.rows(uow, actor, command)
        states = StatesValues.model_validate(prepared.after.unpack()).states
        for row in rows:
            row.read_at = row.read_at or Instant.now() if states[row.id] else None
        await uow.notifications.flush_inbox()
        return tuple(Revision(inbox_target(row.id), row.revision) for row in rows)
