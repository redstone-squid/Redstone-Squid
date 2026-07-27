"""Composition root for framework-neutral application services."""

import random

from squid.db import DatabaseManager
from squid.db.mojang import get_minecraft_username
from squid.db.repos.build_query_repository import BuildMetadataRepository
from squid.db.repos.restriction_repository import RestrictionRepository
from squid.db.repos.settings_repository import SettingsRepository
from squid.db.repos.version_repository import VersionRepository
from squid.db.repos.vote_repository import SQLAlchemyVoteRepository
from squid.db.semantic_search import VecsBuildSearch
from squid.services.build_queries import BuildQueryService
from squid.services.builds import BuildService, RestrictionService
from squid.services.community import (
    RedstonerPolicy,
    RedstonerService,
    WelcomeRelayPolicy,
    WelcomeRelayService,
)
from squid.services.container import ApplicationServices
from squid.services.messages import MessageService
from squid.services.settings import SettingsService
from squid.services.users import UserService
from squid.services.versions import VersionService
from squid.services.votes import VoteService


def create_application_services(db: DatabaseManager) -> ApplicationServices:
    """Create application services from process-level infrastructure."""
    restriction_repository = RestrictionRepository(db.build_tags)
    return ApplicationServices(
        builds=BuildService(db.build, restriction_repository),
        restrictions=RestrictionService(restriction_repository),
        build_queries=BuildQueryService(
            db.build,
            BuildMetadataRepository(db.async_session),
            VecsBuildSearch(),
        ),
        messages=MessageService(db.message_repo),
        settings=SettingsService(SettingsRepository(db.server_setting)),
        users=UserService(
            db.user_repo,
            get_minecraft_username,
            lambda: random.randint(100_000, 999_999),
        ),
        versions=VersionService(VersionRepository(db.async_session)),
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
