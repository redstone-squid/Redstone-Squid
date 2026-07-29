"""Composition root for framework-neutral application services."""

import secrets
from importlib import resources

from squid.builds.application import (
    BuildEmbeddingService,
    BuildInferenceService,
    BuildQueryService,
    BuildService,
    RestrictionService,
)
from squid.builds.infrastructure.embeddings import OpenAIEmbeddingModel, VecsBuildIndex
from squid.builds.infrastructure.queries import BuildMetadataRepository
from squid.builds.infrastructure.repository import BuildRepository
from squid.builds.infrastructure.restrictions import RestrictionRepository
from squid.builds.infrastructure.taxonomy import BuildTagsManager
from squid.builds.infrastructure.text_generation import OpenAITextGenerator
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerPolicy, WelcomeRelayPolicy
from squid.config import RuntimeConfig
from squid.messages.application import MessageService
from squid.messages.infrastructure.repository import MessageRepository
from squid.persistence.engine import DatabaseEngine
from squid.runtime import ApplicationRuntime, ApplicationServices
from squid.settings.application import SettingsService
from squid.settings.infrastructure.repository import SettingsRepository
from squid.users.application import UserService
from squid.users.infrastructure.mojang import get_minecraft_username
from squid.users.infrastructure.repository import UserRepository
from squid.versions.application.services import VersionService
from squid.versions.infrastructure.repository import VersionRepository
from squid.voting.application import VoteService
from squid.voting.infrastructure.repository import VoteRepository


def create_application_services(db: DatabaseEngine, config: RuntimeConfig) -> ApplicationServices:
    """Create application services from process-level infrastructure."""
    restriction_repository = RestrictionRepository(db.async_session)
    version_service = VersionService(VersionRepository(db.async_session))
    embedding_service = BuildEmbeddingService(
        OpenAIEmbeddingModel.from_config(config.embeddings),
        VecsBuildIndex(config.embeddings.database_connection, dimension=config.embeddings.dimension),
    )
    build_repository = BuildRepository(db.async_session)
    return ApplicationServices(
        builds=BuildService(build_repository, restriction_repository, version_service, embedding_service),
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
        settings=SettingsService(SettingsRepository(db.async_session)),
        users=UserService(
            UserRepository(db.async_session, config.verification_code_pepper),
            get_minecraft_username,
            lambda: secrets.randbelow(900_000) + 100_000,
        ),
        versions=version_service,
        votes=VoteService(VoteRepository(db.async_session)),
        redstoner=RedstonerService(
            RedstonerPolicy(
                starboard_author_id=700796664276844612,
                starboard_channel_id=1332630008270684241,
            )
        ),
        welcome_relay=WelcomeRelayService(
            WelcomeRelayPolicy(
                welcome_channel_id=1356094722531393680,
                forward_chance=1 / 10,
            )
        ),
    )


def create_application_runtime(
    config: RuntimeConfig | None = None, db: DatabaseEngine | None = None
) -> ApplicationRuntime:
    """Create the process-owned infrastructure and application service graph."""
    runtime_config = config or RuntimeConfig.from_environment()
    database = db or DatabaseEngine(runtime_config.database)
    return ApplicationRuntime(create_application_services(database, runtime_config), database.close, database.ping)
