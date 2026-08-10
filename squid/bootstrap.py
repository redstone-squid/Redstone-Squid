"""Composition root for framework-neutral application services."""

import logging
import secrets
from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import cached_property, partial
from importlib import resources

from squid.artifacts import ArtifactStore
from squid.artifacts.infrastructure import create_artifact_store
from squid.auth.application import ApiKeyService
from squid.auth.application.web import DiscordOAuthService
from squid.auth.infrastructure import PostgresApiKeyRepository, PostgresWebSessionRepository
from squid.builds.application import (
    BuildEmbeddingService,
    BuildInferenceService,
    BuildQueryService,
    BuildService,
    RestrictionService,
)
from squid.builds.infrastructure.embeddings import OpenAIEmbeddingModel, PostgresBuildIndex
from squid.builds.infrastructure.locks import BuildLockRepository
from squid.builds.infrastructure.queries import BuildMetadataRepository
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.infrastructure.restrictions import RestrictionRepository
from squid.builds.infrastructure.taxonomy import BuildTagsManager
from squid.builds.infrastructure.text_generation import OpenAITextGenerator
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerPolicy, WelcomeRelayPolicy
from squid.config import RuntimeConfig, SchematicConfig
from squid.events.application import DomainEventService
from squid.events.infrastructure.listener import DomainEventWakeListener
from squid.events.infrastructure.repository import PostgresDomainEventRepository
from squid.idempotency import IdempotencyService
from squid.idempotency.infrastructure import PostgresIdempotencyRepository
from squid.messages.application import MessageService
from squid.messages.infrastructure.repository import MessageRepository
from squid.notifications import NotificationService
from squid.notifications.infrastructure.repository import PostgresNotificationRepository
from squid.permissions.application import AuthorizationService
from squid.permissions.infrastructure.repository import GlobalAdministratorRepository
from squid.persistence.engine import DatabaseEngine
from squid.records.application import RecordComputationService, RecordService
from squid.records.infrastructure.repository import PostgresRecordRepository
from squid.runtime import ApiServices, ApplicationRuntime, BotServices, WorkerServices
from squid.schematics.application import (
    RenderRequest,
    SchematicAnalyzer,
    SchematicJobService,
    SchematicRenderJobService,
    SchematicService,
)
from squid.schematics.domain.models import SchematicLimits
from squid.schematics.infrastructure.durable import QueuedSchematicAnalyzer
from squid.schematics.infrastructure.jobs import PostgresSchematicJobRepository
from squid.schematics.infrastructure.render_jobs import PostgresSchematicRenderJobRepository
from squid.schematics.infrastructure.repository import SchematicRepository
from squid.schematics.infrastructure.resource_pack import ResourcePackLoader
from squid.schematics.infrastructure.version_resolver import PostgresSchematicVersionResolver
from squid.search.application import CursorCodec, SearchEmbeddingService, SearchQueryParser, SearchService
from squid.search.infrastructure import (
    PostgresSearchBackend,
    PostgresSearchEmbeddingQueue,
    PostgresSemanticCandidateProvider,
)
from squid.search.infrastructure.fields import PostgresFieldRegistryProvider
from squid.search.infrastructure.projection import run_projection_batch
from squid.settings.application import SettingsService
from squid.settings.infrastructure.repository import SettingsRepository
from squid.starboard.application import StarboardService
from squid.starboard.infrastructure.repository import PostgresStarboardRepository
from squid.sync import DiscordSyncService
from squid.sync.infrastructure import PostgresDiscordSyncQueue
from squid.tags.application import TagService
from squid.tags.infrastructure.repository import PostgresTagDefinitionRepository
from squid.users.application import UserService
from squid.users.infrastructure.mojang import MojangClient
from squid.users.infrastructure.repository import UserRepository
from squid.versions.application.services import VersionService
from squid.versions.infrastructure.repository import VersionRepository
from squid.voting.application import VoteService
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver
from squid.voting.infrastructure.repository import VoteRepository
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
    db: DatabaseEngine,
    config: SchematicConfig,
    artifacts: ArtifactStore,
    jobs: SchematicJobService,
    *,
    render_capable: bool = False,
) -> SchematicService:
    """Assemble the schematic service over whichever analyzer this process can run."""
    analyzer = create_schematic_analyzer(config, jobs, artifacts)
    resource_pack = None
    if render_capable and config.render_enabled:
        resource_pack = ResourcePackLoader(
            path=config.render_pack_path,
            url=str(config.render_pack_url) if config.render_pack_url is not None else None,
            expected_sha256=config.render_pack_sha256,
            cache_dir=config.render_cache_dir,
        )
    return SchematicService(
        analyzer,
        SchematicRepository(db.async_session, artifacts),
        PostgresSchematicVersionResolver(db.async_session),
        limits=SchematicLimits(
            max_upload_bytes=config.max_upload_bytes,
            max_inflated_bytes=config.max_inflated_bytes,
            max_allocated_volume=config.max_allocated_volume,
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
    """Lazily assemble one process's services and register owned adapters."""

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
    def restriction_repository(self) -> RestrictionRepository:
        return RestrictionRepository(self.db.async_session)

    @cached_property
    def version_service(self) -> VersionService:
        return VersionService(VersionRepository(self.db.async_session))

    @cached_property
    def embedding_model(self) -> OpenAIEmbeddingModel:
        model = OpenAIEmbeddingModel.from_config(self.config.embeddings)
        self.resources.push_async_callback(model.aclose)
        return model

    @cached_property
    def embedding_service(self) -> BuildEmbeddingService:
        return BuildEmbeddingService(self.embedding_model, PostgresBuildIndex(self.db.async_session))

    @cached_property
    def build_repository(self) -> BuildRepository:
        return BuildRepository(self.db.async_session)

    @cached_property
    def build_locks(self) -> BuildLockRepository:
        return BuildLockRepository(self.db.async_session)

    @cached_property
    def builds(self) -> BuildService:
        return BuildService(
            self.build_repository,
            self.build_locks,
            self.restriction_repository,
            self.version_service,
            self.embedding_service,
        )

    @cached_property
    def build_queries(self) -> BuildQueryService:
        return BuildQueryService(
            self.build_repository,
            BuildMetadataRepository(self.db.async_session),
            self.embedding_service,
        )

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
            BuildTagsManager(self.db.async_session),
            self.version_service,
            prompt,
        )

    @cached_property
    def record_repository(self) -> PostgresRecordRepository:
        return PostgresRecordRepository(self.db.async_session)

    @cached_property
    def notifications(self) -> NotificationService:
        return NotificationService(
            PostgresNotificationRepository(
                self.db.async_session,
                staff_discord_ids=self.config.notifications.staff_discord_ids,
            ),
            retention_days=self.config.notifications.retention_days,
        )

    @cached_property
    def record_computation(self) -> RecordComputationService:
        return RecordComputationService(self.record_repository, self.record_repository)

    @cached_property
    def records(self) -> RecordService:
        return RecordService(self.record_repository, self.record_repository, self.record_computation)

    @cached_property
    def artifacts(self) -> ArtifactStore:
        store = create_artifact_store(self.config.object_storage)
        self.resources.push_async_callback(store.aclose)
        return store

    @cached_property
    def schematic_jobs(self) -> SchematicJobService:
        return SchematicJobService(
            PostgresSchematicJobRepository(self.db.async_session),
            max_attempts=self.config.schematics.job_max_attempts,
            retention_hours=self.config.schematics.job_retention_hours,
        )

    @cached_property
    def schematics(self) -> SchematicService:
        service = create_schematic_service(
            self.db,
            self.config.schematics,
            self.artifacts,
            self.schematic_jobs,
            render_capable=self.render_capable,
        )
        self.resources.push_async_callback(service.aclose)
        return service

    @cached_property
    def search_fields(self) -> PostgresFieldRegistryProvider:
        return PostgresFieldRegistryProvider(self.db.async_session)

    @cached_property
    def search(self) -> SearchService:
        return SearchService(
            PostgresSearchBackend(
                self.db.async_session,
                fields=self.search_fields,
                semantic_provider=PostgresSemanticCandidateProvider(self.db.async_session, self.embedding_model),
            ),
            SearchQueryParser(),
            CursorCodec(self.config.cursor_secret.get_secret_value().encode()),
            self.search_fields,
        )

    @cached_property
    def search_embeddings(self) -> SearchEmbeddingService:
        return SearchEmbeddingService(
            self.embedding_model,
            PostgresSearchEmbeddingQueue(self.db.async_session),
        )

    @cached_property
    def votes(self) -> VoteService:
        return VoteService(VoteRepository(self.db.async_session))

    @cached_property
    def vote_members(self) -> DiscordRestActorResolver | None:
        if self.config.discord_bot_token is None:
            return None
        resolver = DiscordRestActorResolver(self.config.discord_bot_token.get_secret_value())
        self.resources.push_async_callback(resolver.aclose)
        self.votes.set_actor_resolver(resolver)
        return resolver

    @cached_property
    def users(self) -> UserService:
        mojang = MojangClient()
        self.resources.push_async_callback(mojang.aclose)
        return UserService(
            UserRepository(self.db.async_session, self.config.verification_code_pepper.get_secret_value()),
            mojang.get_username,
            lambda: secrets.randbelow(900_000) + 100_000,
        )

    @cached_property
    def web_auth(self) -> DiscordOAuthService | None:
        if self.config.oauth is None or self.config.session_pepper is None:
            return None
        service = DiscordOAuthService(
            PostgresWebSessionRepository(self.db.async_session),
            self.users,
            self.config.oauth,
            self.config.session_pepper.get_secret_value(),
        )
        self.resources.push_async_callback(service.aclose)
        return service

    @cached_property
    def api_keys(self) -> ApiKeyService | None:
        if self.config.api_key_pepper is None:
            return None
        return ApiKeyService(
            PostgresApiKeyRepository(self.db.async_session),
            self.config.api_key_pepper.get_secret_value(),
        )


def create_api_services(db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack) -> ApiServices:
    """Create only capabilities used by the HTTP API."""
    graph = _ServiceGraph(db, config, resources_stack)
    return ApiServices(
        builds=graph.builds,
        api_keys=graph.api_keys,
        web_auth=graph.web_auth,
        idempotency=IdempotencyService(PostgresIdempotencyRepository(db.async_session)),
        notifications=graph.notifications,
        build_queries=graph.build_queries,
        authorization=AuthorizationService(GlobalAdministratorRepository(db.async_session)),
        records=graph.records,
        schematics=graph.schematics,
        search=graph.search,
        tags=TagService(PostgresTagDefinitionRepository(db.async_session)),
        users=graph.users,
        versions=graph.version_service,
        votes=graph.votes,
        vote_members=graph.vote_members,
    )


def create_bot_services(db: DatabaseEngine, config: RuntimeConfig, resources_stack: AsyncExitStack) -> BotServices:
    """Create only capabilities used by Discord gateway features."""
    graph = _ServiceGraph(db, config, resources_stack)
    return BotServices(
        builds=graph.builds,
        build_inference=graph.build_inference,
        restrictions=RestrictionService(graph.restriction_repository),
        build_queries=graph.build_queries,
        messages=MessageService(MessageRepository(db.async_session)),
        authorization=AuthorizationService(GlobalAdministratorRepository(db.async_session)),
        records=graph.records,
        record_computation=graph.record_computation,
        schematics=graph.schematics,
        search=graph.search,
        tags=TagService(PostgresTagDefinitionRepository(db.async_session)),
        settings=SettingsService(SettingsRepository(db.async_session)),
        starboards=StarboardService(PostgresStarboardRepository(db.async_session)),
        users=graph.users,
        versions=graph.version_service,
        votes=graph.votes,
        discord_sync=DiscordSyncService(PostgresDiscordSyncQueue(db.async_session)),
        domain_events=DomainEventService(PostgresDomainEventRepository(db.async_session)),
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
    """Create only capabilities used by transport-neutral background jobs."""
    graph = _ServiceGraph(db, config, resources_stack, render_capable=True)
    return WorkerServices(
        builds=graph.builds,
        artifacts=graph.artifacts,
        votes=graph.votes,
        records=graph.record_computation,
        events=DomainEventService(PostgresDomainEventRepository(db.async_session)),
        event_wake_listener=(
            None if config.database.listener_url is None else DomainEventWakeListener(config.database.listener_url)
        ),
        notifications=graph.notifications,
        schematics=graph.schematics,
        schematic_jobs=graph.schematic_jobs,
        schematic_renders=SchematicRenderJobService(PostgresSchematicRenderJobRepository(db.async_session)),
        search_embeddings=graph.search_embeddings,
        refresh_search_index=partial(run_projection_batch, db.async_session),
        record_queue_health=PostgresQueueHealthMonitor(db.async_session).record,
    )


def _create_runtime[ServicesT](
    config: RuntimeConfig,
    service_factory: Callable[[DatabaseEngine, RuntimeConfig, AsyncExitStack], ServicesT],
    db: DatabaseEngine | None,
) -> ApplicationRuntime[ServicesT]:
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
