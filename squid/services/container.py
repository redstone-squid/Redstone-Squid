"""Application service container shared by the bot and HTTP API."""

from dataclasses import dataclass

from squid.services.build_inference import BuildInferenceService
from squid.services.build_queries import BuildQueryService
from squid.services.builds import BuildService, RestrictionService
from squid.services.community import RedstonerService, WelcomeRelayService
from squid.services.messages import MessageService
from squid.services.settings import SettingsService
from squid.services.users import UserService
from squid.services.versions import VersionService
from squid.services.votes import VoteService


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
