"""Application service container shared by the bot and HTTP API."""

from dataclasses import dataclass

from squid.community.application import RedstonerService, WelcomeRelayService
from squid.messages.application import MessageService
from squid.services.build_inference import BuildInferenceService
from squid.services.build_queries import BuildQueryService
from squid.services.builds import BuildService, RestrictionService
from squid.services.votes import VoteService
from squid.settings.application import SettingsService
from squid.users.application import UserService
from squid.versions.application.services import VersionService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Long-lived application services created by the process composition root."""

    builds: BuildService
    build_inference: BuildInferenceService
    restrictions: RestrictionService
    build_queries: BuildQueryService
    messages: MessageService
    settings: SettingsService
    users: UserService
    versions: VersionService
    votes: VoteService
    redstoner: RedstonerService
    welcome_relay: WelcomeRelayService
