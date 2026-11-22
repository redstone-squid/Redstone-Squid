"""SQLAlchemy implementation of application-owned transactions."""

from types import TracebackType
from typing import Literal, Self

from anyio import CancelScope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from squid.core.accounts.infrastructure.repository import AccountRepository
from squid.core.auth.infrastructure.repository import PostgresApiKeyRepository
from squid.core.auth.infrastructure.sessions import PostgresWebSessionRepository
from squid.core.builds.infrastructure.embeddings import PostgresBuildIndex
from squid.core.builds.infrastructure.history import BuildHistoryQueries
from squid.core.builds.infrastructure.locks import PostgresBuildLockRepository
from squid.core.builds.infrastructure.repository import BuildRepository
from squid.core.builds.infrastructure.restrictions import RestrictionRepository
from squid.core.builds.infrastructure.tags import PostgresBuildTags
from squid.core.cli_auth.repository import PostgresCliAuthorizationRepository
from squid.core.diagnostics.infrastructure.repository import PostgresErrorReportRepository
from squid.core.edits.domain import ConcurrentEditError
from squid.core.edits.infrastructure import PostgresEditRepository
from squid.core.errors import InvalidStateError
from squid.core.events.infrastructure.repository import PostgresDomainEventRepository, publish_domain_event
from squid.core.idempotency.infrastructure.crypto import IdempotencyResponseCipher
from squid.core.idempotency.infrastructure.repository import PostgresIdempotencyRepository
from squid.core.media.infrastructure.repository import PostgresMediaJobRepository
from squid.core.messages.infrastructure.repository import MessageRepository
from squid.core.minecraft_auth.infrastructure.repository import PostgresMinecraftAuthorizationRepository
from squid.core.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.core.permissions.infrastructure.repository import (
    PostgresPermissionAdminRepository,
    PostgresPermissionRepository,
)
from squid.core.posts.infrastructure.repository import PostRepository
from squid.core.records.infrastructure.range_publications import PostgresRangePublicationRepository
from squid.core.records.infrastructure.repository import PostgresRecordRepository
from squid.core.schematics.infrastructure.jobs import PostgresSchematicJobRepository
from squid.core.schematics.infrastructure.preview_publisher import PostgresSchematicPreviewRepository
from squid.core.schematics.infrastructure.render_jobs import PostgresSchematicRenderJobRepository
from squid.core.schematics.infrastructure.repository import PostgresSchematicRepository
from squid.core.search.infrastructure.embeddings import PostgresSearchEmbeddingQueue
from squid.core.search.infrastructure.fields import PostgresFieldRegistryProvider
from squid.core.search.infrastructure.projection import PostgresSearchProjection
from squid.core.search.infrastructure.repository import PostgresSearchBackend
from squid.core.settings.infrastructure.repository import PostgresSettingsRepository
from squid.core.starboard.infrastructure.repository import PostgresStarboardRepository
from squid.core.submissions.infrastructure.attachment_edits import PostgresAttachmentEdits
from squid.core.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.core.submissions.infrastructure.inference_runs import PostgresInferenceRunRepository
from squid.core.submissions.infrastructure.intake import PostgresAttachmentIntake
from squid.core.submissions.infrastructure.recalculation import PostgresRecalculationProposals
from squid.core.submissions.infrastructure.repository import PostgresDraftRepository
from squid.core.submissions.infrastructure.schematics import PostgresDraftSchematics
from squid.core.suggestions.infrastructure.repository import PostgresSuggestionRepository
from squid.core.sync.infrastructure.repository import PostgresDiscordSyncQueue
from squid.core.tags.infrastructure.repository import PostgresTagDefinitionRepository
from squid.core.unit_of_work import EventFact
from squid.core.versions.infrastructure.repository import VersionRepository
from squid.core.voting.infrastructure.repository import VoteRepository


class SqlAlchemyUnitOfWork:
    """Keep one application operation's repository writes in a single transaction.

    Each exposed repository uses the same SQLAlchemy session. For example, an
    operation can update a build and enqueue a notification, then commit both
    together so a notification is not stored for an update that rolled back.

    Enter once with `async with`, then explicitly call `commit()` to save changes.
    Exiting without a commit rolls back. Repositories can only be used while the
    unit of work is active; committing ends that access even if the commit fails.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        verification_code_pepper: str | None = None,
        idempotency_cipher: IdempotencyResponseCipher | None = None,
    ) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._state: Literal["new", "active", "finished"] = "new"
        self._edits = PostgresEditRepository(self._active_session)
        self._domain_events = PostgresDomainEventRepository(self._active_session)
        self._reconciliation = PostgresDiscordSyncQueue(self._active_session)
        self._notifications = PostgresNotificationRepository(self._active_session)
        self._votes = VoteRepository(self._active_session)
        self._records = PostgresRecordRepository(self._active_session)
        self._record_ranges = PostgresRangePublicationRepository(self._active_session)
        self._idempotency = PostgresIdempotencyRepository(self._active_session, idempotency_cipher)
        self._builds = BuildRepository(self._active_session)
        self._build_history = BuildHistoryQueries(self._active_session)
        self._build_locks = PostgresBuildLockRepository(self._active_session)
        self._submission_drafts = PostgresDraftRepository(self._active_session)
        self._finalization_jobs = PostgresFinalizationJobRepository(self._active_session)
        self._recalculation_proposals = PostgresRecalculationProposals(self._active_session)
        self._attachment_edits = PostgresAttachmentEdits(self._active_session)
        self._attachment_intake = PostgresAttachmentIntake(self._active_session)
        self._draft_schematics = PostgresDraftSchematics(self._active_session)
        self._inference_runs = PostgresInferenceRunRepository(self._active_session)
        self._media_jobs = PostgresMediaJobRepository(self._active_session)
        self._restrictions = RestrictionRepository(self._active_session)
        self._build_tags = PostgresBuildTags(self._active_session)
        self._build_vectors = PostgresBuildIndex(self._active_session)
        self._search = PostgresSearchBackend(self._active_session)
        self._search_fields = PostgresFieldRegistryProvider(self._active_session)
        self._search_embeddings = PostgresSearchEmbeddingQueue(self._active_session)
        self._search_projection = PostgresSearchProjection(self._active_session)
        self._suggestions = PostgresSuggestionRepository(self._active_session)
        self._schematic_jobs = PostgresSchematicJobRepository(self._active_session)
        self._schematic_renders = PostgresSchematicRenderJobRepository(self._active_session)
        self._schematic_previews = PostgresSchematicPreviewRepository(self._active_session)
        self._schematic_metadata = PostgresSchematicRepository(self._active_session)
        self._verification_code_pepper = verification_code_pepper
        self._accounts: AccountRepository | None = None
        self._cli_authorization = PostgresCliAuthorizationRepository(self._active_session)
        self._minecraft_authorization = PostgresMinecraftAuthorizationRepository(self._active_session)
        self._web_sessions = PostgresWebSessionRepository(self._active_session)
        self._api_keys = PostgresApiKeyRepository(self._active_session)
        self._error_reports = PostgresErrorReportRepository(self._active_session)
        self._messages = MessageRepository(self._active_session)
        self._permission_admin = PostgresPermissionAdminRepository(self._active_session)
        self._permissions = PostgresPermissionRepository(self._active_session)
        self._posts = PostRepository(self._active_session)
        self._settings = PostgresSettingsRepository(self._active_session)
        self._starboards = PostgresStarboardRepository(self._active_session)
        self._tags = PostgresTagDefinitionRepository(self._active_session)
        self._versions = VersionRepository(self._active_session)

    @property
    def edits(self) -> PostgresEditRepository:
        self._active_session()
        return self._edits

    @property
    def settings(self) -> PostgresSettingsRepository:
        self._active_session()
        return self._settings

    @property
    def messages(self) -> MessageRepository:
        self._active_session()
        return self._messages

    @property
    def posts(self) -> PostRepository:
        self._active_session()
        return self._posts

    @property
    def versions(self) -> VersionRepository:
        self._active_session()
        return self._versions

    @property
    def tags(self) -> PostgresTagDefinitionRepository:
        self._active_session()
        return self._tags

    @property
    def error_reports(self) -> PostgresErrorReportRepository:
        self._active_session()
        return self._error_reports

    @property
    def starboards(self) -> PostgresStarboardRepository:
        self._active_session()
        return self._starboards

    @property
    def permissions(self) -> PostgresPermissionRepository:
        self._active_session()
        return self._permissions

    @property
    def permission_admin(self) -> PostgresPermissionAdminRepository:
        self._active_session()
        return self._permission_admin

    @property
    def api_keys(self) -> PostgresApiKeyRepository:
        self._active_session()
        return self._api_keys

    @property
    def accounts(self) -> AccountRepository:
        self._active_session()
        if self._accounts is None:
            if self._verification_code_pepper is None:
                message = "Account verification requires a configured pepper."
                raise InvalidStateError(message)
            self._accounts = AccountRepository(self._active_session, self._verification_code_pepper)
        return self._accounts

    @property
    def cli_authorization(self) -> PostgresCliAuthorizationRepository:
        self._active_session()
        return self._cli_authorization

    @property
    def minecraft_authorization(self) -> PostgresMinecraftAuthorizationRepository:
        self._active_session()
        return self._minecraft_authorization

    @property
    def web_sessions(self) -> PostgresWebSessionRepository:
        self._active_session()
        return self._web_sessions

    @property
    def domain_events(self) -> PostgresDomainEventRepository:
        self._active_session()
        return self._domain_events

    @property
    def reconciliation(self) -> PostgresDiscordSyncQueue:
        self._active_session()
        return self._reconciliation

    @property
    def notifications(self) -> PostgresNotificationRepository:
        self._active_session()
        return self._notifications

    @property
    def votes(self) -> VoteRepository:
        self._active_session()
        return self._votes

    @property
    def records(self) -> PostgresRecordRepository:
        self._active_session()
        return self._records

    @property
    def record_ranges(self) -> PostgresRangePublicationRepository:
        self._active_session()
        return self._record_ranges

    @property
    def record_candidates(self) -> PostgresRecordRepository:
        self._active_session()
        return self._records

    @property
    def idempotency(self) -> PostgresIdempotencyRepository:
        self._active_session()
        return self._idempotency

    @property
    def build_history(self) -> BuildHistoryQueries:
        self._active_session()
        return self._build_history

    @property
    def builds(self) -> BuildRepository:
        self._active_session()
        return self._builds

    @property
    def build_locks(self) -> PostgresBuildLockRepository:
        self._active_session()
        return self._build_locks

    @property
    def submission_drafts(self) -> PostgresDraftRepository:
        self._active_session()
        return self._submission_drafts

    @property
    def finalization_jobs(self) -> PostgresFinalizationJobRepository:
        self._active_session()
        return self._finalization_jobs

    @property
    def recalculation_proposals(self) -> PostgresRecalculationProposals:
        self._active_session()
        return self._recalculation_proposals

    @property
    def attachment_edits(self) -> PostgresAttachmentEdits:
        return self._attachment_edits

    @property
    def attachment_intake(self) -> PostgresAttachmentIntake:
        self._active_session()
        return self._attachment_intake

    @property
    def draft_schematics(self) -> PostgresDraftSchematics:
        self._active_session()
        return self._draft_schematics

    @property
    def inference_runs(self) -> PostgresInferenceRunRepository:
        self._active_session()
        return self._inference_runs

    @property
    def media_jobs(self) -> PostgresMediaJobRepository:
        self._active_session()
        return self._media_jobs

    @property
    def restrictions(self) -> RestrictionRepository:
        self._active_session()
        return self._restrictions

    @property
    def build_tags(self) -> PostgresBuildTags:
        self._active_session()
        return self._build_tags

    @property
    def build_vectors(self) -> PostgresBuildIndex:
        self._active_session()
        return self._build_vectors

    @property
    def search(self) -> PostgresSearchBackend:
        self._active_session()
        return self._search

    @property
    def search_fields(self) -> PostgresFieldRegistryProvider:
        self._active_session()
        return self._search_fields

    @property
    def search_embeddings(self) -> PostgresSearchEmbeddingQueue:
        self._active_session()
        return self._search_embeddings

    @property
    def search_projection(self) -> PostgresSearchProjection:
        self._active_session()
        return self._search_projection

    @property
    def suggestions(self) -> PostgresSuggestionRepository:
        self._active_session()
        return self._suggestions

    @property
    def schematic_jobs(self) -> PostgresSchematicJobRepository:
        self._active_session()
        return self._schematic_jobs

    @property
    def schematic_renders(self) -> PostgresSchematicRenderJobRepository:
        self._active_session()
        return self._schematic_renders

    @property
    def schematic_previews(self) -> PostgresSchematicPreviewRepository:
        self._active_session()
        return self._schematic_previews

    @property
    def schematic_metadata(self) -> PostgresSchematicRepository:
        self._active_session()
        return self._schematic_metadata

    def _active_session(self) -> AsyncSession:
        if self._state != "active" or self._session is None:
            message = "The unit of work is not active."
            raise InvalidStateError(message)
        return self._session

    async def __aenter__(self) -> Self:
        if self._state != "new":
            message = "A unit of work can only be entered once."
            raise InvalidStateError(message)
        self._session = self._sessions(expire_on_commit=False)
        try:
            await self._session.begin()
        except BaseException:
            await self._close()
            raise
        self._state = "active"
        return self

    async def emit(self, event: EventFact) -> None:
        await publish_domain_event(self._active_session(), event)

    async def commit(self) -> None:
        """Commit the transaction and stop accepting repository operations."""
        session = self._active_session()
        try:
            await session.commit()
        except StaleDataError as error:
            raise ConcurrentEditError from error
        finally:
            self._state = "finished"

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self._close()

    async def _close(self) -> None:
        self._state = "finished"
        if self._session is not None:
            # Close rolls back unfinished work, including when the caller was cancelled.
            with CancelScope(shield=True):
                await self._session.close()
            self._session = None
