"""Composition root for framework-neutral application services."""

import secrets
from dataclasses import dataclass
from importlib import resources
from types import TracebackType
from typing import Self

from squid.community.application import RedstonerService, WelcomeRelayService
from squid.community.domain import RedstonerPolicy, WelcomeRelayPolicy
from squid.db.build_tags import BuildTagsManager
from squid.db.engine import DatabaseEngine
from squid.db.repos.build_query_repository import BuildMetadataRepository
from squid.db.repos.build_repository import BuildRepository
from squid.db.repos.restriction_repository import RestrictionRepository
from squid.infrastructure.embeddings import OpenAIEmbeddingModel, VecsBuildIndex
from squid.infrastructure.text_generation import OpenAITextGenerator
from squid.messages.application import MessageService
from squid.messages.infrastructure.repository import MessageRepository
from squid.services.build_inference import BuildInferenceService
from squid.services.build_queries import BuildQueryService
from squid.services.builds import BuildService, RestrictionService
from squid.services.container import ApplicationServices
from squid.services.embeddings import BuildEmbeddingService
from squid.settings.application import SettingsService
from squid.settings.infrastructure.repository import SettingsRepository
from squid.users.application import UserService
from squid.users.infrastructure.mojang import get_minecraft_username
from squid.users.infrastructure.repository import UserRepository
from squid.versions.application.services import VersionService
from squid.versions.infrastructure.repository import VersionRepository
from squid.voting.application import VoteService
from squid.voting.infrastructure.repository import VoteRepository


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Own the process-level infrastructure and its application services."""

    db: DatabaseEngine
    services: ApplicationServices

    async def close(self) -> None:
        """Release infrastructure resources owned by the runtime."""
        await self.db.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


def create_application_services(db: DatabaseEngine) -> ApplicationServices:
    """Create application services from process-level infrastructure."""
    restriction_repository = RestrictionRepository(db.async_session)
    version_service = VersionService(VersionRepository(db.async_session))
    embedding_service = BuildEmbeddingService(
        OpenAIEmbeddingModel.from_environment(),
        VecsBuildIndex.from_environment(),
    )
    build_repository = BuildRepository(db.async_session)
    return ApplicationServices(
        builds=BuildService(build_repository, restriction_repository, version_service, embedding_service),
        build_inference=BuildInferenceService(
            OpenAITextGenerator.from_environment(),
            BuildTagsManager(db.async_session),
            version_service,
            resources.files("squid.db").joinpath("prompt.txt").read_text(encoding="utf-8"),
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
            UserRepository(db.async_session),
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


def create_application_runtime(db: DatabaseEngine | None = None) -> ApplicationRuntime:
    """Create the process-owned infrastructure and application service graph."""
    database = db or DatabaseEngine()
    return ApplicationRuntime(database, create_application_services(database))
