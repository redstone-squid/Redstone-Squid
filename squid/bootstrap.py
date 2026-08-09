"""Composition root for framework-neutral application services."""

import logging
import secrets
from functools import partial
from importlib import resources

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
from squid.builds.infrastructure.embeddings import OpenAIEmbeddingModel, VecsBuildIndex
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
from squid.events.infrastructure.repository import PostgresDomainEventRepository
from squid.messages.application import MessageService
from squid.messages.infrastructure.repository import MessageRepository
from squid.permissions.application import AuthorizationService
from squid.permissions.infrastructure.repository import GlobalAdministratorRepository
from squid.persistence.engine import DatabaseEngine
from squid.records.application import RecordComputationService, RecordService
from squid.records.infrastructure.repository import PostgresRecordRepository
from squid.runtime import ApplicationRuntime, ApplicationServices
from squid.schematics.application import RenderRequest, SchematicAnalyzer, SchematicService
from squid.schematics.domain.models import SchematicLimits
from squid.schematics.infrastructure.capability import NullSchematicAnalyzer, engine_installed
from squid.schematics.infrastructure.repository import SchematicRepository
from squid.schematics.infrastructure.resource_pack import ResourcePackLoader
from squid.schematics.infrastructure.version_resolver import PostgresSchematicVersionResolver
from squid.schematics.infrastructure.worker import SchematicWorkerPool
from squid.search.application import CursorCodec, SearchQueryParser, SearchService
from squid.search.infrastructure import PostgresSearchBackend
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
from squid.users.infrastructure.mojang import get_minecraft_username
from squid.users.infrastructure.repository import UserRepository
from squid.versions.application.services import VersionService
from squid.versions.infrastructure.repository import VersionRepository
from squid.voting.application import VoteService
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver
from squid.voting.infrastructure.repository import VoteRepository

logger = logging.getLogger(__name__)


def create_schematic_analyzer(config: SchematicConfig) -> SchematicAnalyzer:
    """Choose the schematic analyzer this process can actually run.

    The engine is an optional extra with no musl or linux-aarch64 wheels, so absence is a
    supported deployment rather than a misconfiguration: the null analyzer raises one typed,
    translated error from every method and the rest of the bot is unaffected.

    Availability is decided with `find_spec`, never a trial import — loading a native extension
    is expensive and an ABI-mismatched wheel can abort the interpreter. The real import happens
    only in the worker child, where a failure is contained.
    """
    if not config.enabled:
        return NullSchematicAnalyzer("Schematic support is switched off by configuration.")
    if not engine_installed():
        logger.info("The optional schematic engine is not installed; schematic features are disabled.")
        return NullSchematicAnalyzer("The optional 'schematics' extra is not installed.")
    return SchematicWorkerPool(config)


def create_schematic_service(db: DatabaseEngine, config: SchematicConfig) -> SchematicService:
    """Assemble the schematic service over whichever analyzer this process can run."""
    analyzer = create_schematic_analyzer(config)
    resource_pack = None
    if config.render_pack_path is not None or config.render_pack_url is not None:
        resource_pack = ResourcePackLoader(
            path=config.render_pack_path,
            url=str(config.render_pack_url) if config.render_pack_url is not None else None,
            expected_sha256=config.render_pack_sha256,
            cache_dir=config.render_cache_dir,
        )
    return SchematicService(
        analyzer,
        SchematicRepository(db.async_session),
        PostgresSchematicVersionResolver(db.async_session),
        limits=SchematicLimits(
            max_upload_bytes=config.max_upload_bytes,
            max_inflated_bytes=config.max_inflated_bytes,
            max_allocated_volume=config.max_allocated_volume,
        ),
        engine_installed=not isinstance(analyzer, NullSchematicAnalyzer),
        duplicate_metric_tolerance=config.duplicate_metric_tolerance,
        duplicate_near_distance=config.duplicate_near_distance,
        duplicate_max_comparisons=config.duplicate_max_comparisons,
        duplicate_result_limit=config.duplicate_result_limit,
        duplicate_total_timeout_seconds=config.duplicate_total_timeout_seconds,
        render_enabled=config.render_enabled,
        resource_pack=resource_pack,
        render_request=RenderRequest(
            width=config.render_width,
            height=config.render_height,
            background=config.render_background,
        ),
        render_max_block_count=config.render_max_block_count,
        render_max_bounding_volume=config.render_max_bounding_volume,
    )


def create_application_services(db: DatabaseEngine, config: RuntimeConfig) -> ApplicationServices:
    """Create application services from process-level infrastructure."""
    restriction_repository = RestrictionRepository(db.async_session)
    version_service = VersionService(VersionRepository(db.async_session))
    embedding_service = BuildEmbeddingService(
        OpenAIEmbeddingModel.from_config(config.embeddings),
        VecsBuildIndex(
            config.embeddings.database_connection.get_secret_value()
            if config.embeddings.database_connection is not None
            else None
        ),
    )
    build_repository = BuildRepository(db.async_session)
    build_locks = BuildLockRepository(db.async_session)
    record_repository = PostgresRecordRepository(db.async_session)
    record_computation = RecordComputationService(record_repository, record_repository)
    search_fields = PostgresFieldRegistryProvider(db.async_session)
    vote_service = VoteService(VoteRepository(db.async_session))
    vote_members = (
        DiscordRestActorResolver(config.discord_bot_token.get_secret_value())
        if config.discord_bot_token is not None
        else None
    )
    if vote_members is not None:
        vote_service.set_actor_resolver(vote_members)
    user_service = UserService(
        UserRepository(db.async_session, config.verification_code_pepper.get_secret_value()),
        get_minecraft_username,
        lambda: secrets.randbelow(900_000) + 100_000,
    )
    return ApplicationServices(
        builds=BuildService(build_repository, build_locks, restriction_repository, version_service, embedding_service),
        api_keys=(
            ApiKeyService(PostgresApiKeyRepository(db.async_session), config.api_key_pepper.get_secret_value())
            if config.api_key_pepper is not None
            else None
        ),
        web_auth=(
            DiscordOAuthService(
                PostgresWebSessionRepository(db.async_session),
                user_service,
                config.oauth,
                config.session_pepper.get_secret_value(),
            )
            if config.oauth is not None and config.session_pepper is not None
            else None
        ),
        build_inference=BuildInferenceService(
            OpenAITextGenerator.from_config(config.openai),
            BuildTagsManager(db.async_session),
            version_service,
            resources.files("squid.builds.infrastructure").joinpath("prompt.txt").read_text(encoding="utf-8"),
        ),
        restrictions=RestrictionService(restriction_repository),
        build_queries=BuildQueryService(
            build_repository,
            BuildMetadataRepository(db.async_session),
            embedding_service,
        ),
        messages=MessageService(MessageRepository(db.async_session)),
        authorization=AuthorizationService(GlobalAdministratorRepository(db.async_session)),
        records=RecordService(record_repository, record_repository, record_computation),
        record_computation=record_computation,
        schematics=create_schematic_service(db, config.schematics),
        search=SearchService(
            PostgresSearchBackend(db.async_session, fields=search_fields),
            SearchQueryParser(),
            CursorCodec(config.cursor_secret.get_secret_value().encode()),
            search_fields,
        ),
        tags=TagService(PostgresTagDefinitionRepository(db.async_session)),
        refresh_search_index=partial(run_projection_batch, db.async_session),
        settings=SettingsService(SettingsRepository(db.async_session)),
        starboards=StarboardService(PostgresStarboardRepository(db.async_session)),
        users=user_service,
        versions=version_service,
        votes=vote_service,
        vote_members=vote_members,
        discord_sync=DiscordSyncService(PostgresDiscordSyncQueue(db.async_session)),
        domain_events=DomainEventService(PostgresDomainEventRepository(db.async_session)),
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


def create_application_runtime(config: RuntimeConfig, db: DatabaseEngine | None = None) -> ApplicationRuntime:
    """Create the process-owned infrastructure and application service graph."""
    database = db or DatabaseEngine(config.database)
    services = create_application_services(database, config)

    async def close_resources() -> None:
        """Stop the schematic workers before the database, so an in-flight analysis that
        wants to write its result still has a session to write it with."""
        await services.schematics.aclose()
        if services.vote_members is not None:
            await services.vote_members.aclose()
        if services.web_auth is not None:
            await services.web_auth.aclose()
        await database.close()

    return ApplicationRuntime(services, close_resources, database.ping)
