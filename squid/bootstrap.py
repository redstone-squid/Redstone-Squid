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

from squid.accounts.application import AccountService
from squid.accounts.application.services import generate_verification_code
from squid.accounts.infrastructure.mojang import MojangClient
from squid.artifacts import ArtifactStore
from squid.artifacts.infrastructure import create_artifact_store
from squid.auth.application import ApiKeyService
from squid.auth.application.providers import PROVIDER_FACTORIES, OAuthProvider
from squid.auth.application.web import WebSessionService
from squid.builds.application import (
    BuildEmbeddingService,
    BuildInferenceService,
    BuildQueryService,
    BuildService,
    RestrictionService,
)
from squid.builds.application.locks import BuildLockService
from squid.builds.application.taxonomy_service import BuildTaxonomyService
from squid.builds.infrastructure.embeddings import OpenAIEmbeddingModel
from squid.builds.infrastructure.text_generation import OpenAITextGenerator
from squid.cli_auth import CliAuthorizationService, CliSecretCodec
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerPolicy, WelcomeRelayPolicy
from squid.config import BotIdentityConfig, RuntimeConfig, SchematicConfig
from squid.diagnostics.application import ErrorReportService
from squid.events.application import DomainEventService
from squid.events.infrastructure.listener import DomainEventWakeListener
from squid.idempotency import IdempotencyService
from squid.idempotency.infrastructure import IdempotencyResponseCipher
from squid.media.application.jobs import MediaNormalizationJobRunner, MediaNormalizationJobService, MediaStorageCleanup
from squid.media.application.services import MediaNormalizationService
from squid.media.domain import MediaLimits
from squid.media.infrastructure import FfmpegMediaNormalizer, MediaProcessLimits
from squid.messages.application import MessageService
from squid.minecraft_auth.application import InstallationCredentialService, PlayerAuthorizationService
from squid.minecraft_auth.application.crypto import MinecraftSecretCodec
from squid.notifications import NotificationService
from squid.permissions.application import (
    PermissionAdministrationService,
    PermissionEpochWatcher,
    PermissionService,
    SubjectRuleCache,
)
from squid.permissions.infrastructure.repository import (
    EPOCH_CHANNEL,
)
from squid.persistence.engine import DatabaseEngine
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.persistence.wake_listener import PostgresWakeListener
from squid.posts.application import PostService
from squid.records.application import PublicRecordQueryService, RecordComputationService, RecordService
from squid.runtime import ApiServices, ApplicationRuntime, BotServices, WorkerServices
from squid.schematics.application import (
    RenderRequest,
    SchematicAnalyzer,
    SchematicJobService,
    SchematicRenderJobService,
    SchematicService,
)
from squid.schematics.application.preview_storage import SchematicPreviewStorage
from squid.schematics.application.storage import SchematicStorageService
from squid.schematics.application.versions import SchematicVersionService
from squid.schematics.domain.models import SchematicLimits
from squid.schematics.infrastructure.durable import QueuedSchematicAnalyzer
from squid.schematics.infrastructure.resource_pack import ResourcePackLoader
from squid.search.application import SearchEmbeddingService, SearchService
from squid.search.application.projection import SearchProjectionService
from squid.settings.application import SettingsService
from squid.starboard.application import StarboardService
from squid.submissions.application import (
    CheckedInFormManifestRegistry,
    SubmissionDraftService,
    SubmissionFinalizationService,
    SubmissionFinalizationWorker,
    SubmissionFormService,
    SubmissionPreparation,
)
from squid.submissions.application.artifact_readiness import (
    AuthoritativeDraftArtifactReadiness,
)
from squid.submissions.application.commit import BuildSubmissionExecutor
from squid.submissions.application.inference_runs import SubmissionInferenceRuns
from squid.submissions.application.intake import SubmissionAttachmentIntake
from squid.submissions.application.revisions import RevisionProposalService
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.infrastructure.build_target import SubmissionBuildPreparation
from squid.submissions.infrastructure.inference_codec import PydanticInferenceRunCodec
from squid.submissions.infrastructure.sponsors import PaperSponsorResolver
from squid.submissions.infrastructure.suggestion_options import SuggestionFormOptionCatalog
from squid.suggestions.application import SuggestionService
from squid.suggestions.application.queries import SuggestionQueryService
from squid.suggestions.infrastructure.catalogue import build_registry as build_suggestion_registry
from squid.sync import DiscordReconciliationService
from squid.tags.application import TagService
from squid.unit_of_work import UnitOfWorkFactory
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService
from squid.voting.application.recount import BallotRecounter
from squid.voting.infrastructure.accounts import AccountConsentedVoterLookup, AccountVoterDiscordIdLookup
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver, DiscordRestVoteCards
from squid.worker.queue_health import PostgresQueueHealthMonitor

logger = logging.getLogger(__name__)


def create_schematic_analyzer(
    config: SchematicConfig,
    jobs: SchematicJobService,
    artifacts: ArtifactStore,
) -> SchematicAnalyzer:
    """Build a queue client; only the dedicated worker process owns native execution."""
    return QueuedSchematicAnalyzer(jobs, artifacts, config)


def create_schematic_service(
    config: SchematicConfig,
    artifacts: ArtifactStore,
    jobs: SchematicJobService,
    *,
    uow_factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    render_capable: bool = False,
) -> SchematicService:
    """Build a schematic service that submits work to the durable queue.

    When `render_capable` and rendering are enabled, attach a resource-pack loader
    so this process can prepare render requests. Native rendering still runs in
    the dedicated worker process.
    """
    analyzer = create_schematic_analyzer(config, jobs, artifacts)
    resource_pack = None
    if render_capable and config.render_enabled:
        resource_pack = ResourcePackLoader(
            path=config.render_pack_path,
            url=str(config.render_pack_url) if config.render_pack_url is not None else None,
            expected_sha256=config.render_pack_sha256,
            cache_dir=config.render_cache_dir,
        )
    previews = SchematicPreviewStorage(artifacts, uow_factory=uow_factory)
    return SchematicService(
        analyzer,
        SchematicStorageService(artifacts, uow_factory=uow_factory),
        previews,
        SchematicVersionService(uow_factory=uow_factory),
        limits=SchematicLimits(
            max_upload_bytes=config.max_upload_bytes,
            max_inflated_bytes=config.max_inflated_bytes,
            max_allocated_volume=config.max_allocated_volume,
            max_axis_length=config.max_axis_length,
        ),
        engine_installed=config.enabled,
        duplicate_metric_tolerance=config.duplicate_metric_tolerance,
        duplicate_near_distance=config.duplicate_near_distance,
        duplicate_max_comparisons=config.duplicate_max_comparisons,
        duplicate_result_limit=config.duplicate_result_limit,
        duplicate_total_timeout_seconds=config.duplicate_total_timeout_seconds,
        render_enabled=config.render_enabled and render_capable,
        resource_pack=resource_pack,
        render_request=RenderRequest(
            width=config.render_width,
            height=config.render_height,
            background=config.render_background,
        ),
        render_max_block_count=config.render_max_block_count,
        render_max_bounding_volume=config.render_max_bounding_volume,
    )


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
        *,
        render_capable: bool = False,
    ) -> None:
        self.db = db
        self.config = config
        self.resources = resources_stack
        self.render_capable = render_capable

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
    def build_taxonomy(self) -> BuildTaxonomyService:
        return BuildTaxonomyService(uow_factory=self.uow_factory)

    @cached_property
    def permission_cache(self) -> SubjectRuleCache:
        return SubjectRuleCache()

    @cached_property
    def permissions(self) -> PermissionService:
        return PermissionService(uow_factory=self.uow_factory, cache=self.permission_cache)

    @cached_property
    def permission_admin(self) -> PermissionAdministrationService:
        return PermissionAdministrationService(self.permissions, uow_factory=self.uow_factory)

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
            self.embedding_service,
            self.build_taxonomy,
            permissions=self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def build_queries(self) -> BuildQueryService:
        return BuildQueryService(self.embedding_service, uow_factory=self.uow_factory)

    @cached_property
    def text_generator(self) -> OpenAITextGenerator:
        generator = OpenAITextGenerator.from_config(self.config.openai)
        self.resources.push_async_callback(generator.aclose)
        return generator

    @cached_property
    def build_inference(self) -> BuildInferenceService:
        prompt = resources.files("squid.builds.infrastructure").joinpath("prompt.txt").read_text(encoding="utf-8")
        return BuildInferenceService(
            self.text_generator,
            self.build_taxonomy,
            self.version_service,
            prompt,
        )

    @cached_property
    def notifications(self) -> NotificationService:
        return NotificationService(
            uow_factory=self.uow_factory,
            retention_days=self.config.notifications.retention_days,
        )

    @cached_property
    def record_computation(self) -> RecordComputationService:
        return RecordComputationService(uow_factory=self.uow_factory)

    @cached_property
    def records(self) -> RecordService:
        return RecordService(self.record_computation, uow_factory=self.uow_factory)

    @cached_property
    def public_records(self) -> PublicRecordQueryService:
        return PublicRecordQueryService(self.build_queries, uow_factory=self.uow_factory)

    @cached_property
    def artifacts(self) -> ArtifactStore:
        store = create_artifact_store(self.config.object_storage)
        self.resources.push_async_callback(store.aclose)
        return store

    @cached_property
    def media_job_service(self) -> MediaNormalizationJobService:
        """Build durable media metadata services even when normalization is disabled."""
        return MediaNormalizationJobService(
            self.artifacts,
            uow_factory=self.uow_factory,
            limits=MediaLimits(),
            max_attempts=self.config.media.job_max_attempts,
        )

    @cached_property
    def media_jobs(self) -> MediaNormalizationJobService | None:
        if not self.config.media.enabled:
            return None
        return self.media_job_service

    @cached_property
    def media_cleanup(self) -> MediaStorageCleanup:
        return MediaStorageCleanup(self.media_job_service, self.artifacts)

    @cached_property
    def media_runner(self) -> MediaNormalizationJobRunner | None:
        jobs = self.media_jobs
        if jobs is None:
            return None
        media = self.config.media
        normalization = MediaNormalizationService(
            FfmpegMediaNormalizer(
                ffmpeg=media.ffmpeg,
                ffprobe=media.ffprobe,
                process_limits=MediaProcessLimits(
                    probe_timeout_seconds=media.probe_timeout_seconds,
                    image_timeout_seconds=media.image_timeout_seconds,
                    video_timeout_seconds=media.video_timeout_seconds,
                    poster_timeout_seconds=media.poster_timeout_seconds,
                    memory_bytes=media.memory_bytes,
                    cpu_seconds=media.cpu_seconds,
                    max_open_files=media.max_open_files,
                    threads=media.threads,
                ),
            ),
            limits=jobs.limits,
        )
        self.resources.push_async_callback(normalization.aclose)
        return MediaNormalizationJobRunner(
            jobs,
            self.artifacts,
            normalization,
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
            render_capable=self.render_capable,
            uow_factory=self.uow_factory,
        )
        self.resources.push_async_callback(service.aclose)
        return service

    @cached_property
    def tags(self) -> TagService:
        return TagService(uow_factory=self.uow_factory)

    @cached_property
    def submission_manifests(self) -> CheckedInFormManifestRegistry:
        return CheckedInFormManifestRegistry()

    @cached_property
    def submission_forms(self) -> SubmissionFormService:
        return SubmissionFormService(SuggestionFormOptionCatalog(self.suggestions), self.submission_manifests)

    @cached_property
    def submission_drafts(self) -> SubmissionDraftService:
        return SubmissionDraftService(
            self.submission_manifests,
            inferred_capacity=self.config.submissions.inferred_draft_capacity,
            permissions=self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def submission_inference(self) -> SubmissionInferenceRuns:
        return SubmissionInferenceRuns(
            self.build_inference,
            self.artifacts,
            PydanticInferenceRunCodec(),
            uow_factory=self.uow_factory,
            capacity=self.config.submissions.inferred_draft_capacity,
            permissions=self.permissions,
        )

    @cached_property
    def submission_intake(self) -> SubmissionAttachmentIntake:
        return SubmissionAttachmentIntake(
            self.submission_drafts, self.submission_schematics, self.media_jobs, uow_factory=self.uow_factory
        )

    @cached_property
    def submission_revisions(self) -> RevisionProposalService:
        return RevisionProposalService(
            self.builds,
            self.permissions,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def submission_schematics(self) -> DraftSchematicService:
        return DraftSchematicService(self.submission_drafts, self.artifacts, uow_factory=self.uow_factory)

    @cached_property
    def submission_finalization(self) -> SubmissionFinalizationService:
        readiness = AuthoritativeDraftArtifactReadiness(
            self.media_job_service,
            self.submission_schematics,
            intake=self.submission_intake,
            media_limits=MediaLimits(),
        )
        sponsors = (
            PaperSponsorResolver(self.minecraft_installations)
            if self.config.minecraft_auth.sponsor_attribution_enabled and self.minecraft_installations is not None
            else None
        )
        return SubmissionFinalizationService(
            self.submission_drafts,
            SubmissionPreparation(readiness, sponsors),
            uow_factory=self.uow_factory,
        )

    @cached_property
    def submission_finalization_worker(self) -> SubmissionFinalizationWorker:
        return SubmissionFinalizationWorker(
            BuildSubmissionExecutor(
                SubmissionBuildPreparation(self.builds, self.tags, self.version_service),
                drafts=self.submission_drafts,
                uow_factory=self.uow_factory,
            ),
            preparation=self.submission_finalization,
            uow_factory=self.uow_factory,
        )

    @cached_property
    def search(self) -> SearchService:
        return SearchService(
            uow_factory=self.uow_factory,
            embedding_model=self.embedding_model,
        )

    @cached_property
    def search_projection(self) -> SearchProjectionService:
        return SearchProjectionService(uow_factory=self.uow_factory)

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
    def vote_members(self) -> DiscordRestActorResolver | None:
        if self.config.discord_bot_token is None:
            return None
        resolver = DiscordRestActorResolver(
            self.config.discord_bot_token.get_secret_value(),
            capabilities=self.permissions,
            discord_ids=AccountVoterDiscordIdLookup(self.accounts),
            api_url=str(self.config.upstream_http.discord_api_url),
        )
        self.resources.push_async_callback(resolver.aclose)
        self.votes.set_actor_resolver(resolver)
        return resolver

    @cached_property
    def rest_ballot_recounter(self) -> BallotRecounter | None:
        """Recount ballots through Discord's REST API, for processes without a gateway."""
        resolver = self.vote_members
        if resolver is None or self.config.discord_bot_token is None:
            return None
        cards = DiscordRestVoteCards(
            self.config.discord_bot_token.get_secret_value(),
            api_url=str(self.config.upstream_http.discord_api_url),
        )
        self.resources.push_async_callback(cards.aclose)
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
    graph = _ServiceGraph(db, config, resources_stack, render_capable=True)
    encryption = config.idempotency_encryption
    if encryption is None:
        msg = "The API runtime requires an idempotency response-encryption keyring."
        raise RuntimeError(msg)
    return ApiServices(
        builds=graph.builds,
        error_reports=graph.error_reports,
        api_keys=graph.api_keys,
        web_auth=graph.web_auth,
        cli_authorization=graph.cli_authorization,
        idempotency=IdempotencyService(uow_factory=graph.uow_factory),
        notifications=graph.notifications,
        build_queries=graph.build_queries,
        permissions=graph.permissions,
        permission_epoch=graph.permission_epoch,
        records=graph.records,
        public_records=graph.public_records,
        schematics=graph.schematics,
        search=graph.search,
        tags=graph.tags,
        submission_forms=graph.submission_forms,
        submission_drafts=graph.submission_drafts,
        submission_finalization=graph.submission_finalization,
        submission_schematics=graph.submission_schematics,
        submission_revisions=graph.submission_revisions,
        submission_intake=graph.submission_intake,
        submission_inference=graph.submission_inference,
        suggestions=graph.suggestions,
        media_jobs=graph.media_jobs,
        minecraft_installations=graph.minecraft_installations,
        minecraft_player_authorization=graph.minecraft_player_authorization,
        accounts=graph.accounts,
        versions=graph.version_service,
        votes=graph.votes,
        vote_members=graph.vote_members,
    )


def create_bot_services(db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack) -> BotServices:
    """Create only capabilities used by Discord gateway features."""
    graph = _ServiceGraph(db, config, resources_stack, render_capable=True)
    return BotServices(
        builds=graph.builds,
        error_reports=graph.error_reports,
        build_inference=graph.build_inference,
        submission_forms=graph.submission_forms,
        submission_drafts=graph.submission_drafts,
        submission_finalization=graph.submission_finalization,
        submission_schematics=graph.submission_schematics,
        submission_revisions=graph.submission_revisions,
        submission_intake=graph.submission_intake,
        submission_inference=graph.submission_inference,
        restrictions=graph.restrictions,
        build_queries=graph.build_queries,
        messages=MessageService(uow_factory=graph.uow_factory),
        posts=PostService(uow_factory=graph.uow_factory),
        permissions=graph.permissions,
        permission_admin=graph.permission_admin,
        permission_epoch=graph.permission_epoch,
        records=graph.records,
        record_computation=graph.record_computation,
        schematics=graph.schematics,
        search=graph.search,
        tags=graph.tags,
        settings=SettingsService(uow_factory=graph.uow_factory),
        starboards=graph.starboards,
        suggestions=graph.bot_suggestions,
        accounts=graph.accounts,
        versions=graph.version_service,
        votes=graph.votes,
        discord_reconciliation=DiscordReconciliationService(uow_factory=graph.uow_factory),
        domain_events=DomainEventService(uow_factory=graph.uow_factory),
        notifications=graph.notifications,
        redstoner=RedstonerService(
            RedstonerPolicy(
                starboard_author_id=config.community.redstoner_starboard_author_id,
                starboard_channel_id=config.community.redstoner_starboard_channel_id,
            )
        ),
        welcome_relay=WelcomeRelayService(
            WelcomeRelayPolicy(
                welcome_channel_id=config.community.welcome_channel_id,
                forward_chance=1 / 10,
            )
        ),
    )


def create_worker_services(
    db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack
) -> WorkerServices:
    """Create only capabilities used by background jobs, which serve no request."""
    graph = _ServiceGraph(db, config, resources_stack, render_capable=True)
    idempotency = IdempotencyService(uow_factory=graph.uow_factory)
    return WorkerServices(
        builds=graph.builds,
        error_reports=graph.error_reports,
        artifacts=graph.artifacts,
        votes=graph.votes,
        ballot_recounter=graph.rest_ballot_recounter,
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
        submission_finalization=graph.submission_finalization_worker,
        search_embeddings=graph.search_embeddings,
        refresh_search_index=graph.search_projection.process_batch,
        record_queue_health=PostgresQueueHealthMonitor(db.async_session).record,
        purge_idempotency=idempotency.purge_expired,
        expire_submission_drafts=graph.submission_drafts.expire_due,
        cleanup_submission_schematics=graph.submission_schematics.cleanup,
        cleanup_submission_inference=graph.submission_inference.cleanup,
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
