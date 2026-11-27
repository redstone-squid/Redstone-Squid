"""Application data consumed by the simulated Discord workspaces."""

from collections.abc import Sequence
from typing import override

import discord
import simcord
from whenever import Instant

from squid.core.accounts.domain import PrivacyNoticeReceipt
from squid.core.actors import ActingContext, UserActor
from squid.core.diagnostics.application import ErrorReportService
from squid.core.diagnostics.domain import EVERY_REPORT, ErrorReportFilters
from squid.core.diagnostics.models import ErrorReport
from squid.core.notifications import (
    NotificationPreferences,
    NotificationService,
    PendingNotificationDelivery,
)
from squid.core.notifications.application import Inbox, InboxEntry, NotificationEditorSnapshot
from squid.core.notifications.domain import NotificationKind, NotificationMessage, NotifiedBuild
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, offset_page
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.application.ports import PermissionSubject, SubjectRecords
from squid.core.permissions.domain import Effect, PermissionNode
from squid.core.permissions.models import PermissionGrant
from squid.core.settings.application import SettingsService
from squid.core.settings.domain.guild import GuildSettings
from tests.support.accounts import FakeAccountRepository
from tests.support.permissions import permission_uow_factory


def staff_member(env: simcord.Env, accounts: FakeAccountRepository) -> simcord.MemberActor:
    role = env.guild.create_role("Staff", permissions=discord.Permissions(manage_guild=True))
    staff = env.guild.add_member(env.create_user("staff"), roles=[role])
    accounts.seed_account(staff.id, account_id=27, consent=PrivacyNoticeReceipt.grant_current())
    return staff


class WorkspaceRules:
    def __init__(self, nodes: tuple[PermissionNode, ...]) -> None:
        self.nodes = nodes

    async def load_for_subject(self, subject: PermissionSubject) -> SubjectRecords:
        grants = tuple(
            PermissionGrant(pattern=node.name, effect=Effect.ALLOW, subject_account_id=subject.account_id)
            for node in self.nodes
            if isinstance(subject, UserActor) and subject.discord_guild_admin and subject.account_id is not None
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
        return NotificationEditorSnapshot(NotificationPreferences(account_id), ())

    @override
    async def inbox(
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> Inbox:
        account_id = context.effective.require_account_id()
        assert account_id == self.account_id
        inbox_item = InboxEntry(
            3, 1, Instant.now(), None, NotificationMessage(NotificationKind.BUILD_CONFIRMED, NotifiedBuild(9))
        )
        return Inbox(Page(items=(inbox_item,), total=1, next=None, prev=None), unread_count=1)

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
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        filters: ErrorReportFilters = EVERY_REPORT,
    ) -> Page[ErrorReport]:
        self.recent_calls.append((page_size, filters.work_lost_only))
        return offset_page((self.report,), offset=selector.offset, page_size=page_size)
