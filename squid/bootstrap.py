"""Construct each process's services and register their cleanup callbacks.

The API, bot, and worker use different service bundles, but many services need
the same database, permission cache, or external client. _ServiceGraph creates
each dependency on first use and shares it within one bundle. The create_*_runtime
functions return an ApplicationRuntime that owns the resulting resources.

The runtime owns its database even when the caller supplies it. It closes adapters
before the database so their shutdown work can still use a database connection.
"""

import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import cached_property, partial
from importlib import resources

import httpx
from whenever import Instant

from squid.config import BotIdentityConfig, RuntimeConfig
from squid.core.accounts.application import AccountService
from squid.core.accounts.application.edits import (
    PROFILE_CLEAR,
    PROFILE_EDIT,
    UNLINK_EDIT,
    VISIBILITY_EDIT,
    AccountProfileClear,
    AccountProfileEdit,
    AccountUnlinkEdit,
    AccountVisibilityEdit,
)
from squid.core.accounts.application.services import generate_verification_code
from squid.core.accounts.infrastructure.mojang import MojangClient
from squid.core.actors import ActingContext
from squid.core.artifacts import ArtifactStore
from squid.core.artifacts.infrastructure import create_artifact_store
from squid.core.auth.application import ApiKeyService
from squid.core.auth.application.providers import PROVIDER_FACTORIES, OAuthProvider
from squid.core.auth.application.viewer import ViewerQueries
from squid.core.auth.application.web import WebSessionService
from squid.core.auth.impersonation import ImpersonationService
from squid.core.builds.application import (
    BuildEmbeddingService,
    BuildInferenceService,
    BuildQueryService,
    BuildService,
    RestrictionService,
)
from squid.core.builds.application.category_edits import BUILD_CATEGORY_EDIT, BuildCategoryEdit
from squid.core.builds.application.edits import BUILD_EDIT, BUILD_STATUS_EDIT, BuildFieldsEdit, BuildStatusEdit
from squid.core.builds.application.locks import BuildLockService
from squid.core.builds.application.tag_service import BuildTagService
from squid.core.builds.infrastructure.embeddings import OpenAIEmbeddingModel
from squid.core.builds.infrastructure.schematic_summary import SchematicServiceSummaryProvider
from squid.core.builds.infrastructure.text_generation import OpenAITextGenerator
from squid.core.cli_auth import CliAuthorizationService, CliSecretCodec
from squid.core.community.application import WelcomeRelayService
from squid.core.community.domain import WelcomeRelayPolicy
from squid.core.diagnostics.application import ErrorReportService
from squid.core.edits.application import EditExecutor
from squid.core.events.application import DomainEventService
from squid.core.events.infrastructure.listener import DomainEventWakeListener
from squid.core.idempotency import IdempotencyService
from squid.core.idempotency.infrastructure import IdempotencyResponseCipher
from squid.core.media.application.build_media import PublishedBuildMedia
from squid.core.media.application.jobs import MediaProcessingJobRunner, MediaProcessingJobService, MediaStorageCleanup
from squid.core.media.application.services import MediaProcessingService
from squid.core.media.domain import MediaLimits
from squid.core.media.infrastructure import FfmpegMediaProcessor, MediaProcessLimits
from squid.core.messages.application import MessageService
from squid.core.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.core.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.core.notifications import NotificationService
from squid.core.notifications.edits import (
    INBOX_EDIT,
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    InboxReadEdit,
    NotificationPreferencesEdit,
    NotificationSubscriptionsEdit,
)
from squid.core.permissions.application import (
    PermissionAdministrationService,
    PermissionEpochWatcher,
    PermissionService,
    SubjectRuleCache,
)
from squid.core.permissions.application.edits import (
    ASSIGNMENT_EDIT,
    BULK_ASSIGNMENT_EDIT,
    GRANT_EDIT,
    INCLUDE_EDIT,
    PATTERN_EDIT,
    RANK_EDIT,
    ROLE_CREATE,
    ROLE_DELETE,
    PermissionAssignmentBatchEdit,
    PermissionAssignmentEdit,
    PermissionGrantEdit,
    PermissionIncludeEdit,
    PermissionPatternEdit,
    PermissionRankEdit,
    PermissionRoleCreation,
    PermissionRoleDeletion,
)
from squid.core.permissions.infrastructure.repository import (
    EPOCH_CHANNEL,
)
from squid.core.persistence.engine import DatabaseEngine
from squid.core.persistence.topic_bridge import open_topic_bridge
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.persistence.wake_listener import PostgresWakeListener
from squid.core.posts.application import PostService
from squid.core.records.application import (
    PublicRecordQueryService,
    RecordAdministrationService,
    RecordComputationService,
    RecordService,
)
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from squid.core.schematics.application import SchematicJobService, SchematicRenderJobService, SchematicService
from squid.core.schematics.bootstrap import create_schematic_service
from squid.core.search.application import SearchEmbeddingService, SearchService
from squid.core.search.application.projection import refresh_search_index
from squid.core.settings.application import SettingsService
from squid.core.settings.application.discord import RuntimeDiscordReferences
from squid.core.settings.application.edits import GUILD_EDIT, RUNTIME_EDIT, GuildSettingsEdit, RuntimeSettingsEdit
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.settings.infrastructure.discord_rest import DiscordRestDirectory
from squid.core.starboard.application import StarboardService
from squid.core.starboard.application.edits import (
    STARBOARD_CREATE,
    STARBOARD_DELETE,
    STARBOARD_EDIT,
    StarboardCreation,
    StarboardDeletion,
    StarboardEdit,
)
from squid.core.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, DraftAttachmentsEdit
from squid.core.submissions.application.edits import DRAFT_ANSWERS, DraftAnswersEdit
from squid.core.submissions.bootstrap import SubmissionServices
from squid.core.suggestions.application import SuggestionService
from squid.core.suggestions.application.queries import SuggestionQueryService
from squid.core.suggestions.infrastructure.catalogue import build_registry as build_suggestion_registry
from squid.core.sync import DiscordReconciliationService
from squid.core.tags.application import TagService
from squid.core.tags.application.edits import (
    ALIAS_EDIT,
    MODERATION_EDIT,
    PROPOSAL_EDIT,
    TagAliasEdit,
    TagModerationEdit,
    TagProposalEdit,
)
from squid.core.unit_of_work import UnitOfWorkFactory
from squid.core.versions.application.services import VersionService
from squid.core.voting.application import VoteService
from squid.core.voting.application.accounts import AccountConsentedVoterLookup, AccountVoterDiscordIdLookup
from squid.core.voting.application.recount import BallotRecounter
from squid.core.voting.infrastructure.discord_cards import DiscordVoteCards
from squid.core.voting.infrastructure.discord_rest import DiscordRestActorResolver
from squid.discord_rest import RestOnlyClient, rest_client
from squid.runtime import ApiServices, ApplicationRuntime, BotServices, WorkerServices
from squid.worker.queue_health import PostgresQueueHealthMonitor

logger = logging.getLogger(__name__)


class _ServiceGraph:
    """Build each service once per process and register adapter cleanup.

    Cached properties let services share dependencies without constructing unused
    adapters. The caller must close the supplied AsyncExitStack on shutdown.
    """

    def __init__(
        self,
        db: DatabaseEngine,
        config: RuntimeConfig,
        resources_stack: AsyncExitStack,
    ) -> None:
        self.db = db
        self.config = config
        self.resources = resources_stack

    @cached_property
    def uow_factory(self) -> UnitOfWorkFactory[SqlAlchemyUnitOfWork]:
        return partial(
            SqlAlchemyUnitOfWork,
            self.db.async_session,
            verification_code_pepper=self.config.verification_code_pepper.get_secret_value(),
            idempotency_cipher=self.idempotency_cipher,
        )

    @cached_property
    def idempotency_cipher(self) -> IdempotencyResponseCipher | None:
        encryption = self.config.idempotency_encryption
        if encryption is None:
            return None
        return IdempotencyResponseCipher(active_key_id=encryption.active_key_id, keys=encryption.decoded_keys())

    @cached_property
    def restrictions(self) -> RestrictionService:
        return RestrictionService(uow_factory=self.uow_factory)

    @cached_property
    def build_tags(self) -> BuildTagService:
        return BuildTagService(uow_factory=self.uow_factory)

    @cached_property
    def permission_cache(self) -> SubjectRuleCache:
        return SubjectRuleCache()

    @cached_property
    def permissions(self) -> PermissionService:
        return PermissionService(uow_factory=self.uow_factory, cache=self.permission_cache)

    @cached_property
    def permission_admin(self) -> PermissionAdministrationService:
        return PermissionAdministrationService(
            self.permissions, uow_factory=self.uow_factory, members=self.vote_members
        )

    @cached_property
    def permission_epoch(self) -> PermissionEpochWatcher:
        """Watch permission changes using the same cache as this process's checks.

        Polling notices grants written by other processes. The optional PostgreSQL
        listener wakes the watcher sooner.
        """
        listener = (
            None
            if self.config.database.listener_url is None
            else PostgresWakeListener(self.config.database.listener_url, channel=EPOCH_CHANNEL)
        )
        return PermissionEpochWatcher(self.permission_cache, uow_factory=self.uow_factory, listener=listener)

    @cached_property
    def version_service(self) -> VersionService:
        return VersionService(uow_factory=self.uow_factory)

    @cached_property
    def embedding_model(self) -> OpenAIEmbeddingModel:
        model = OpenAIEmbeddingModel.from_config(self.config.embeddings)
        self.resources.push_async_callback(model.aclose)
        return model

    @cached_property
    def embedding_service(self) -> BuildEmbeddingService:
        return BuildEmbeddingService(self.embedding_model, uow_factory=self.uow_factory)

    @cached_property
    def build_locks(self) -> BuildLockService:
        return BuildLockService(uow_factory=self.uow_factory)

    @cached_property
    def builds(self) -> BuildService:
        return BuildService(
            self.build_locks,
            self.restrictions,
            self.version_service,
            self.build_tags,
            permissions=self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def build_queries(self) -> BuildQueryService:
        return BuildQueryService(self.embedding_service, self.permissions, uow_factory=self.uow_factory)

    @cached_property
    def text_generator(self) -> OpenAITextGenerator:
        generator = OpenAITextGenerator.from_config(self.config.openai)
        self.resources.push_async_callback(generator.aclose)
        return generator

    @cached_property
    def build_inference(self) -> BuildInferenceService:
        prompt = resources.files("squid.core.builds.infrastructure").joinpath("prompt.txt").read_text(encoding="utf-8")
        return BuildInferenceService(
            self.text_generator,
            self.build_tags,
            self.version_service,
            prompt,
        )

    @cached_property
    def settings(self) -> SettingsService:
        return SettingsService(
            uow_factory=self.uow_factory, permissions=self.permissions, discord=self.runtime_discord_references
        )

    @cached_property
    def runtime_discord_references(self) -> RuntimeDiscordReferences:
        client = self.discord_rest_client
        return RuntimeDiscordReferences(
            None if client is None else DiscordRestDirectory(client),
            owner_guild_id=BotIdentityConfig().owner_server_id,
        )

    @cached_property
    def edits(self) -> EditExecutor[SqlAlchemyUnitOfWork]:
        return EditExecutor(
            self.uow_factory,
            {
                DRAFT_ATTACHMENTS: DraftAttachmentsEdit(self.submissions.drafts, self.media_job_service.limits),
                DRAFT_ANSWERS: DraftAnswersEdit(self.submissions.drafts),
                ALIAS_EDIT: TagAliasEdit(self.permissions),
                PROPOSAL_EDIT: TagProposalEdit(),
                MODERATION_EDIT: TagModerationEdit(self.permissions),
                GRANT_EDIT: PermissionGrantEdit(self.permission_admin),
                BULK_ASSIGNMENT_EDIT: PermissionAssignmentBatchEdit(self.permission_admin),
                ASSIGNMENT_EDIT: PermissionAssignmentEdit(self.permission_admin),
                RANK_EDIT: PermissionRankEdit(self.permission_admin),
                PATTERN_EDIT: PermissionPatternEdit(self.permission_admin),
                INCLUDE_EDIT: PermissionIncludeEdit(self.permission_admin),
                ROLE_CREATE: PermissionRoleCreation(self.permission_admin),
                ROLE_DELETE: PermissionRoleDeletion(self.permission_admin),
                BUILD_EDIT: BuildFieldsEdit(self.builds),
                BUILD_STATUS_EDIT: BuildStatusEdit(self.permissions),
                BUILD_CATEGORY_EDIT: BuildCategoryEdit(self.permissions),
                RUNTIME_EDIT: RuntimeSettingsEdit(self.permissions, RuntimeSettings(), self.runtime_discord_references),
                GUILD_EDIT: GuildSettingsEdit(self.permissions),
                PROFILE_CLEAR: AccountProfileClear(self.permissions),
                PROFILE_EDIT: AccountProfileEdit(self.permissions),
                VISIBILITY_EDIT: AccountVisibilityEdit(self.permissions),
                UNLINK_EDIT: AccountUnlinkEdit(self.permissions),
                STARBOARD_EDIT: StarboardEdit(self.permissions),
                STARBOARD_CREATE: StarboardCreation(self.permissions),
                STARBOARD_DELETE: StarboardDeletion(self.permissions),
                PREFERENCES_EDIT: NotificationPreferencesEdit(self.permissions),
                SUBSCRIPTIONS_EDIT: NotificationSubscriptionsEdit(self.permissions),
                INBOX_EDIT: InboxReadEdit(self.permissions),
            },
        )

    @cached_property
    def notifications(self) -> NotificationService:
        return NotificationService(
            permissions=self.permissions,
            uow_factory=self.uow_factory,
            runtime_settings=self.settings.get_runtime,
        )

    @cached_property
    def record_computation(self) -> RecordComputationService:
        return RecordComputationService(uow_factory=self.uow_factory)

    @cached_property
    def range_publication(self) -> RangePublicationService:
        return RangePublicationService(
            self.record_computation,
            self.permissions,
            uow_factory=self.uow_factory,
            analyzer=SubprocessRangeAnalyzer(),
            builds=self.build_queries,
        )

    @cached_property
    def records(self) -> RecordService:
        return RecordService(
            self.record_computation,
            permissions=self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def public_records(self) -> PublicRecordQueryService:
        return PublicRecordQueryService(self.build_queries, uow_factory=self.uow_factory)

    @cached_property
    def artifacts(self) -> ArtifactStore:
        store = create_artifact_store(self.config.object_storage)
        self.resources.push_async_callback(store.aclose)
        return store

    @cached_property
    def published_build_media(self) -> PublishedBuildMedia | None:
        base_url = self.config.media.public_base_url or self.config.schematics.render_public_base_url
        if base_url is None:
            return None
        return PublishedBuildMedia(self.uow_factory, self.artifacts, public_base_url=str(base_url))

    @cached_property
    def media_job_service(self) -> MediaProcessingJobService:
        """Build durable media metadata services even when processing is disabled."""
        return MediaProcessingJobService(
            self.artifacts,
            uow_factory=self.uow_factory,
            limits=MediaLimits(),
            max_attempts=self.config.media.job_max_attempts,
        )

    @cached_property
    def media_jobs(self) -> MediaProcessingJobService | None:
        if not self.config.media.enabled:
            return None
        return self.media_job_service

    @cached_property
    def media_cleanup(self) -> MediaStorageCleanup:
        return MediaStorageCleanup(self.media_job_service, self.artifacts)

    @cached_property
    def media_runner(self) -> MediaProcessingJobRunner | None:
        jobs = self.media_jobs
        if jobs is None:
            return None
        media = self.config.media
        processing = MediaProcessingService(
            FfmpegMediaProcessor(
                ffmpeg=media.ffmpeg,
                ffprobe=media.ffprobe,
                process_limits=MediaProcessLimits(
                    probe_timeout_seconds=media.probe_timeout_seconds,
                    poster_timeout_seconds=media.poster_timeout_seconds,
                    memory_bytes=media.memory_bytes,
                    cpu_seconds=media.cpu_seconds,
                    max_open_files=media.max_open_files,
                    threads=media.threads,
                ),
            ),
            limits=jobs.limits,
        )
        self.resources.push_async_callback(processing.aclose)
        return MediaProcessingJobRunner(
            jobs,
            self.artifacts,
            processing,
            working_directory=media.working_directory,
        )

    @cached_property
    def schematic_jobs(self) -> SchematicJobService:
        return SchematicJobService(
            uow_factory=self.uow_factory,
            max_attempts=self.config.schematics.job_max_attempts,
            retention_hours=self.config.schematics.job_retention_hours,
        )

    @cached_property
    def schematics(self) -> SchematicService:
        service = create_schematic_service(
            self.config.schematics,
            self.artifacts,
            self.schematic_jobs,
            permissions=self.permissions,
            render_capable=True,
            uow_factory=self.uow_factory,
        )
        self.resources.push_async_callback(service.aclose)
        return service

    @cached_property
    def tags(self) -> TagService:
        return TagService(uow_factory=self.uow_factory)

    @cached_property
    def submissions(self) -> SubmissionServices:
        return SubmissionServices(
            uow_factory=self.uow_factory,
            permissions=self.permissions,
            builds=self.builds,
            build_inference=self.build_inference,
            artifacts=self.artifacts,
            schematics=self.schematics,
            media_metadata=self.media_job_service,
            media_jobs=self.media_jobs,
            suggestions=self.suggestions,
            tags=self.tags,
            version_service=self.version_service,
            minecraft_installations=(
                self.minecraft_installations if self.config.minecraft_auth.sponsor_attribution_enabled else None
            ),
            inferred_capacity=self.config.submissions.inferred_draft_capacity,
            max_schematic_bytes=self.config.schematics.max_upload_bytes,
        )

    @cached_property
    def search(self) -> SearchService:
        return SearchService(
            uow_factory=self.uow_factory,
            builds=self.build_queries,
            embedding_model=self.embedding_model,
        )

    @cached_property
    def suggestion_queries(self) -> SuggestionQueryService:
        return SuggestionQueryService(uow_factory=self.uow_factory)

    @cached_property
    def suggestions(self) -> SuggestionService:
        """Suggestion sources available to every process."""
        return SuggestionService(
            build_suggestion_registry(
                repository=self.suggestion_queries,
                search=self.search,
                versions=self.version_service,
                tags=self.tags,
                notifications=self.notifications,
                accounts=self.accounts,
            )
        )

    @cached_property
    def bot_suggestions(self) -> SuggestionService:
        """Suggestions plus the guild-scoped sources only the gateway process can answer."""
        return SuggestionService(
            build_suggestion_registry(
                repository=self.suggestion_queries,
                search=self.search,
                versions=self.version_service,
                tags=self.tags,
                notifications=self.notifications,
                accounts=self.accounts,
                starboards=self.starboards,
                permission_roles=self.permission_admin,
            )
        )

    @cached_property
    def starboards(self) -> StarboardService:
        return StarboardService(uow_factory=self.uow_factory)

    @cached_property
    def search_embeddings(self) -> SearchEmbeddingService:
        return SearchEmbeddingService(
            self.embedding_model,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def votes(self) -> VoteService:
        # Build reviews are shared across every guild with a vote channel, so their
        # weights answer to the network's own server. BotIdentityConfig is
        # code-owned, so reading it here matches what the bot itself uses.
        return VoteService(
            uow_factory=self.uow_factory,
            build_owner_guild_id=BotIdentityConfig().owner_server_id,
        )

    @cached_property
    def discord_rest_client(self) -> RestOnlyClient | None:
        """The gateway-less bot-token client shared by every adapter, so they share rate-limit accounting."""
        if self.config.discord_bot_token is None:
            return None
        client = rest_client(
            self.config.discord_bot_token.get_secret_value(),
            api_url=str(self.config.upstream_http.discord_api_url),
        )
        self.resources.push_async_callback(client.close)
        return client

    @cached_property
    def vote_members(self) -> DiscordRestActorResolver | None:
        client = self.discord_rest_client
        if client is None:
            return None
        resolver = DiscordRestActorResolver(
            client,
            permissions=self.permissions,
            discord_ids=AccountVoterDiscordIdLookup(self.accounts),
        )
        self.votes.set_actor_resolver(resolver)
        return resolver

    @cached_property
    def rest_ballot_recounter(self) -> BallotRecounter | None:
        """Recount ballots through Discord's REST API, for processes without a gateway."""
        resolver = self.vote_members
        client = self.discord_rest_client
        if resolver is None or client is None:
            return None
        cards = DiscordVoteCards(client)
        return BallotRecounter(self.votes, cards, AccountConsentedVoterLookup(self.accounts), resolver)

    @cached_property
    def accounts(self) -> AccountService:
        mojang = MojangClient(profile_url=str(self.config.upstream_http.mojang_profile_url))
        self.resources.push_async_callback(mojang.aclose)
        return AccountService(
            mojang.get_username,
            generate_verification_code,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def minecraft_installations(self) -> InstallationCredentialService | None:
        pepper = self.config.minecraft_auth.pepper
        if pepper is None:
            return None
        return InstallationCredentialService(
            MinecraftSecretCodec(pepper.get_secret_value().encode()),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def minecraft_player_authorization(self) -> PlayerAuthorizationService | None:
        pepper = self.config.minecraft_auth.pepper
        if pepper is None:
            return None
        return PlayerAuthorizationService(
            MinecraftSecretCodec(pepper.get_secret_value().encode()),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def web_auth(self) -> WebSessionService | None:
        """Browser login over every provider this deployment has credentials for.

        The httpx client is owned here rather than by the service, so adding a second
        provider shares one connection pool instead of opening a second.
        """
        if self.config.oauth is None or self.config.session_pepper is None:
            return None
        clients = self.config.oauth.clients()
        if not clients:
            return None
        http = httpx.AsyncClient(timeout=10)
        self.resources.push_async_callback(http.aclose)
        providers: dict[str, OAuthProvider] = {}
        for provider, credentials in clients.items():
            factory = PROVIDER_FACTORIES.get(provider)
            if factory is None:
                continue
            adapter = factory(credentials, http, upstreams=self.config.upstream_http)
            providers[adapter.slug] = adapter
        return WebSessionService(
            providers,
            self.config.oauth.session_ttl_hours,
            self.config.session_pepper.get_secret_value(),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def cli_authorization(self) -> CliAuthorizationService | None:
        pepper = self.config.cli_auth.pepper
        if pepper is None:
            return None
        return CliAuthorizationService(
            CliSecretCodec(pepper.get_secret_value().encode()),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def error_reports(self) -> ErrorReportService:
        return ErrorReportService(
            permissions=self.permissions,
            uow_factory=self.uow_factory,
            retention_hours=self.config.diagnostics.retention_hours,
            max_traceback_chars=self.config.diagnostics.max_traceback_chars,
        )

    @cached_property
    def api_keys(self) -> ApiKeyService | None:
        if self.config.api_key_pepper is None:
            return None
        return ApiKeyService(
            self.config.api_key_pepper.get_secret_value(),
            uow_factory=self.uow_factory,
            permissions=self.permissions,
        )


def create_api_services(db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack) -> ApiServices:
    """Create only capabilities used by the HTTP API."""
    graph = _ServiceGraph(db, config, resources_stack)
    encryption = config.idempotency_encryption
    if encryption is None:
        msg = "The API runtime requires an idempotency response-encryption keyring."
        raise RuntimeError(msg)
    return ApiServices(
        impersonation=ImpersonationService(graph.uow_factory, graph.permissions),
        viewer_queries=ViewerQueries(graph.uow_factory, graph.permissions),
        settings=graph.settings,
        edits=graph.edits,
        builds=graph.builds,
        error_reports=graph.error_reports,
        api_keys=graph.api_keys,
        web_auth=graph.web_auth,
        cli_authorization=graph.cli_authorization,
        idempotency=IdempotencyService(uow_factory=graph.uow_factory),
        notifications=graph.notifications,
        build_queries=graph.build_queries,
        permissions=graph.permissions,
        permission_admin=graph.permission_admin,
        permission_epoch=graph.permission_epoch,
        records=graph.records,
        record_administration=RecordAdministrationService(
            graph.record_computation, graph.permissions, uow_factory=graph.uow_factory
        ),
        public_records=graph.public_records,
        range_publication=graph.range_publication,
        schematics=graph.schematics,
        search=graph.search,
        tags=graph.tags,
        submission_forms=graph.submissions.forms,
        submission_drafts=graph.submissions.drafts,
        submission_finalization=graph.submissions.finalization,
        submission_schematics=graph.submissions.draft_schematics,
        submission_recalculations=graph.submissions.recalculations,
        submission_intake=graph.submissions.intake,
        submission_inference=graph.submissions.inference,
        suggestions=graph.suggestions,
        media_jobs=graph.media_jobs,
        published_build_media=graph.published_build_media,
        minecraft_installations=graph.minecraft_installations,
        minecraft_player_authorization=graph.minecraft_player_authorization,
        accounts=graph.accounts,
        versions=graph.version_service,
        votes=graph.votes,
        vote_members=graph.vote_members,
    )


def create_bot_services(db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack) -> BotServices:
    """Create only capabilities used by Discord gateway features."""
    graph = _ServiceGraph(db, config, resources_stack)
    return BotServices(
        impersonation=ImpersonationService(graph.uow_factory, graph.permissions),
        builds=graph.builds,
        error_reports=graph.error_reports,
        build_inference=graph.build_inference,
        submission_forms=graph.submissions.forms,
        submission_drafts=graph.submissions.drafts,
        submission_finalization=graph.submissions.finalization,
        submission_schematics=graph.submissions.draft_schematics,
        submission_recalculations=graph.submissions.recalculations,
        submission_intake=graph.submissions.intake,
        submission_uploads=graph.submissions.uploads,
        submission_inference=graph.submissions.inference,
        restrictions=graph.restrictions,
        build_queries=graph.build_queries,
        messages=MessageService(uow_factory=graph.uow_factory),
        posts=PostService(uow_factory=graph.uow_factory),
        permissions=graph.permissions,
        permission_admin=graph.permission_admin,
        permission_epoch=graph.permission_epoch,
        records=graph.records,
        public_records=graph.public_records,
        record_administration=RecordAdministrationService(
            graph.record_computation, graph.permissions, uow_factory=graph.uow_factory
        ),
        schematics=graph.schematics,
        published_build_media=graph.published_build_media,
        schematic_summaries=SchematicServiceSummaryProvider(
            graph.schematics,
            public_base_url=(
                str(graph.config.schematics.render_public_base_url)
                if graph.config.schematics.render_public_base_url
                else None
            ),
        ),
        search=graph.search,
        tags=graph.tags,
        settings=graph.settings,
        edits=graph.edits,
        starboards=graph.starboards,
        suggestions=graph.bot_suggestions,
        accounts=graph.accounts,
        versions=graph.version_service,
        votes=graph.votes,
        vote_cards=DiscordVoteCards,
        discord_reconciliation=DiscordReconciliationService(uow_factory=graph.uow_factory),
        domain_events=DomainEventService(uow_factory=graph.uow_factory),
        notifications=graph.notifications,
        open_topic_bridge=partial(open_topic_bridge, config.database),
        welcome_relay=WelcomeRelayService(
            WelcomeRelayPolicy(
                welcome_channel_id=RuntimeSettings().welcome_channel_id,
                forward_chance=RuntimeSettings().welcome_forward_chance,
            )
        ),
    )


def create_worker_services(
    db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack
) -> WorkerServices:
    """Create only capabilities used by background jobs, which serve no request."""
    graph = _ServiceGraph(db, config, resources_stack)
    idempotency = IdempotencyService(uow_factory=graph.uow_factory)

    async def purge_mutation_outcomes(*, context: ActingContext) -> int:
        removed = await idempotency.purge_expired(context=context)
        async with graph.uow_factory() as uow:
            await uow.edits.purge_expired(now=Instant.now())
            await uow.commit()
        return removed

    return WorkerServices(
        builds=graph.builds,
        error_reports=graph.error_reports,
        artifacts=graph.artifacts,
        votes=graph.votes,
        ballot_recounter=graph.rest_ballot_recounter,
        range_publication=graph.range_publication,
        records=graph.record_computation,
        events=DomainEventService(uow_factory=graph.uow_factory),
        event_wake_listener=(
            None if config.database.listener_url is None else DomainEventWakeListener(config.database.listener_url)
        ),
        notifications=graph.notifications,
        schematics=graph.schematics,
        schematic_jobs=graph.schematic_jobs,
        schematic_renders=SchematicRenderJobService(uow_factory=graph.uow_factory),
        media_runner=graph.media_runner,
        media_cleanup=graph.media_cleanup,
        submission_finalization=graph.submissions.finalization_worker,
        index_build_embedding=lambda event: graph.embedding_service.handle(event, context=ActingContext.current()),
        search_embeddings=graph.search_embeddings,
        refresh_search_index=partial(refresh_search_index, graph.uow_factory),
        record_queue_health=PostgresQueueHealthMonitor(db.async_session).record,
        purge_idempotency=purge_mutation_outcomes,
        expire_submission_drafts=graph.submissions.drafts.expire_due,
        cleanup_submission_schematics=graph.submissions.draft_schematics.cleanup,
        process_submission_schematics=graph.submissions.draft_schematics.process_pending,
        cleanup_submission_inference=graph.submissions.inference.cleanup,
        open_topic_bridge=partial(open_topic_bridge, config.database),
    )


def _create_runtime[ServicesT](
    config: RuntimeConfig,
    service_factory: Callable[[DatabaseEngine, RuntimeConfig, AsyncExitStack], ServicesT],
    db: DatabaseEngine | None,
) -> ApplicationRuntime[ServicesT]:
    """Create a runtime that closes its adapters and then its database, including a supplied database."""
    database = db or DatabaseEngine(config.database)
    resources_stack = AsyncExitStack()
    # Registered first so LIFO shutdown keeps the database alive until every adapter
    # that may finish an in-flight write has closed.
    resources_stack.push_async_callback(database.close)
    services = service_factory(database, config, resources_stack)
    return ApplicationRuntime(services, resources_stack.aclose, database.ping, database.check_readiness)


def create_api_runtime(config: RuntimeConfig, db: DatabaseEngine | None = None) -> ApplicationRuntime[ApiServices]:
    """Create process-owned API infrastructure and services."""
    return _create_runtime(config, create_api_services, db)


def create_bot_runtime(config: RuntimeConfig, db: DatabaseEngine | None = None) -> ApplicationRuntime[BotServices]:
    """Create process-owned Discord infrastructure and services."""
    return _create_runtime(config, create_bot_services, db)


def create_worker_runtime(
    config: RuntimeConfig, db: DatabaseEngine | None = None
) -> ApplicationRuntime[WorkerServices]:
    """Create process-owned worker infrastructure and services."""
    return _create_runtime(config, create_worker_services, db)
