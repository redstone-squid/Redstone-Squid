"""Composition root for framework-neutral application services."""

import random
from dataclasses import dataclass
from importlib import resources
from types import TracebackType
from typing import Self

from squid.db import DatabaseManager
from squid.db.mojang import get_minecraft_username
from squid.db.repos.build_query_repository import BuildMetadataRepository
from squid.db.repos.restriction_repository import RestrictionRepository
from squid.db.repos.settings_repository import SettingsRepository
from squid.db.repos.version_repository import VersionRepository
from squid.db.repos.vote_repository import SQLAlchemyVoteRepository
from squid.infrastructure.embeddings import OpenAIEmbeddingModel, VecsBuildIndex
from squid.infrastructure.text_generation import OpenAITextGenerator
from squid.services.build_inference import BuildInferenceService
from squid.services.build_queries import BuildQueryService
from squid.services.builds import BuildService, RestrictionService
from squid.services.community import (
    RedstonerPolicy,
    RedstonerService,
    WelcomeRelayPolicy,
    WelcomeRelayService,
)
from squid.services.container import ApplicationServices
from squid.services.embeddings import BuildEmbeddingService
from squid.services.messages import MessageService
from squid.services.settings import SettingsService
from squid.services.users import UserService
from squid.services.versions import VersionService
from squid.services.votes import VoteService


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Own the process-level infrastructure and its application services."""

    db: DatabaseManager
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


def create_application_services(db: DatabaseManager) -> ApplicationServices:
    """Create application services from process-level infrastructure."""
    restriction_repository = RestrictionRepository(db.async_session)
    version_service = VersionService(VersionRepository(db.async_session))
    embedding_service = BuildEmbeddingService(
        OpenAIEmbeddingModel.from_environment(),
        VecsBuildIndex.from_environment(),
    )
    return ApplicationServices(
        builds=BuildService(db.build, restriction_repository, version_service, embedding_service),
        build_inference=BuildInferenceService(
            OpenAITextGenerator.from_environment(),
            db.build_tags,
            version_service,
            resources.files("squid.db").joinpath("prompt.txt").read_text(encoding="utf-8"),
        ),
        restrictions=RestrictionService(restriction_repository),
        build_queries=BuildQueryService(
            db.build,
            BuildMetadataRepository(db.async_session),
            embedding_service,
        ),
        messages=MessageService(db.message_repo),
        settings=SettingsService(SettingsRepository(db.async_session)),
        users=UserService(
            db.user_repo,
            get_minecraft_username,
            lambda: random.randint(100_000, 999_999),
        ),
        versions=version_service,
        votes=VoteService(SQLAlchemyVoteRepository(db.async_session)),
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


def create_application_runtime(db: DatabaseManager | None = None) -> ApplicationRuntime:
    """Create the process-owned infrastructure and application service graph."""
    database = db or DatabaseManager()
    return ApplicationRuntime(database, create_application_services(database))
