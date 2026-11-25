"""Application data consumed by the simulated Discord workspaces."""

from collections.abc import Sequence
from typing import override
from uuid import UUID

import discord
import simcord
from whenever import Instant

from squid.core.accounts.application import AccountService
from squid.core.accounts.application.edits import AccountEditorSnapshot
from squid.core.accounts.domain import AccountConsent, IdentityProvider
from squid.core.accounts.models import Account, AccountProfile
from squid.core.actors import ActingContext, Actor
from squid.core.diagnostics.application import ErrorReportService
from squid.core.diagnostics.models import ErrorReport
from squid.core.notifications import (
    NotificationPreferences,
    NotificationService,
    PendingNotificationDelivery,
)
from squid.core.notifications.application import NotificationEditorSnapshot
from squid.core.notifications.domain import NotificationKind
from squid.core.notifications.models import InboxNotification
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.application.ports import SubjectRecords
from squid.core.permissions.domain import Effect, PermissionNode
from squid.core.permissions.models import PermissionGrant
from squid.core.settings.application import SettingsService
from squid.core.settings.domain.guild import GuildSettings
from tests.support.permissions import permission_uow_factory


class WorkspaceAccounts(AccountService):
    def __init__(self) -> None:
        self.discord_accounts: dict[str, Account] = {}
        self.lookups: list[str] = []

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        assert provider is IdentityProvider.DISCORD
        self.lookups.append(subject)
        return self.discord_accounts.get(subject)

    @override
    async def credit_owners(self, build_id: int, *, context: ActingContext) -> dict[int, UUID]:
        del build_id
        return {}

    @override
    async def editor_snapshot(self, account_id: int, *, context: ActingContext) -> AccountEditorSnapshot:
        account = next(account for account in self.discord_accounts.values() if account.id == account_id)
        return AccountEditorSnapshot(account, AccountProfile.empty(account_id))


def staff_member(env: simcord.Env, accounts: WorkspaceAccounts) -> simcord.MemberActor:
    role = env.guild.create_role("Staff", permissions=discord.Permissions(manage_guild=True))
    staff = env.guild.add_member(env.create_user("staff"), roles=[role])
    accounts.discord_accounts[str(staff.id)] = Account(id=27, consent=AccountConsent.grant_current())
    return staff


class WorkspaceRules:
    def __init__(self, nodes: tuple[PermissionNode, ...]) -> None:
        self.nodes = nodes

    async def load_for_subject(self, subject: Actor) -> SubjectRecords:
        grants = tuple(
            PermissionGrant(pattern=node.name, effect=Effect.ALLOW, subject_account_id=subject.account_id)
            for node in self.nodes
            if subject.discord_guild_admin and subject.account_id is not None
        )
        return SubjectRecords(1, subject, grants=grants)

    async def epoch(self) -> int:
        return 1


def staff_permissions(*nodes: PermissionNode) -> PermissionService:
    return PermissionService(uow_factory=permission_uow_factory(WorkspaceRules(nodes)))


class AccessRevision(PermissionAdministrationService):
    def __init__(self) -> None:
        pass

    @override
    async def revision(self, *, context: ActingContext) -> int:
        return 1


class GuildConfiguration(SettingsService):
    def __init__(self) -> None:
        self.snapshots: dict[int, GuildSettings] = {}

    @override
    async def get_locale(self, server_id: int, *, context: ActingContext) -> str | None:
        return None

    @override
    async def guild_snapshot(self, guild_id: int, *, context: ActingContext) -> tuple[GuildSettings, int]:
        return self.snapshots[guild_id], 1


class WorkspaceInbox(NotificationService):
    def __init__(self, account_id: int) -> None:
        self.account_id = account_id

    @override
    async def editor_snapshot(self, *, context: ActingContext) -> NotificationEditorSnapshot:
        account_id = context.effective.require_account_id()
        assert account_id == self.account_id
        return NotificationEditorSnapshot(NotificationPreferences(account_id, consent_pending=False), ())

    @override
    async def inbox(
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[InboxNotification]:
        account_id = context.effective.require_account_id()
        assert account_id == self.account_id
        inbox_item = InboxNotification(
            id=3, kind=NotificationKind.BUILD_CONFIRMED, payload={"build_id": 9}, created_at=Instant.now()
        )
        return Page(items=(inbox_item,), total=1, next=None, prev=None)

    @override
    async def claim_deliveries(
        self, *, context: ActingContext, limit: int = 20
    ) -> Sequence[PendingNotificationDelivery]:
        return ()


class StoredReports(ErrorReportService):
    def __init__(self) -> None:
        self.recent_calls: list[tuple[int, bool]] = []
        self.report = ErrorReport(
            reference="37a81f",
            correlation_id="37a81f-command",
            surface="app_command",
            origin="build submit",
            occurred_at=Instant.from_utc(2026, 11, 19, 13),
            expires_at=Instant.from_utc(2026, 11, 26, 13),
            exception_type="ValueError",
            message="Unknown block",
            traceback="ValueError: Unknown block",
        )

    @override
    async def lookup(self, reference: str, *, context: ActingContext, prefix: bool = False) -> tuple[ErrorReport, int]:
        assert reference == self.report.reference
        assert prefix
        return self.report, 1

    @override
    async def recent(
        self, *, context: ActingContext, limit: int = 20, work_lost_only: bool = False
    ) -> tuple[ErrorReport, ...]:
        self.recent_calls.append((limit, work_lost_only))
        return (self.report,)
