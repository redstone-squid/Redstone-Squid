"""Shared SQLAlchemy infrastructure and model registration."""

from squid.builds.infrastructure import models as build_models
from squid.messages.infrastructure import models as message_models
from squid.records.infrastructure import models as record_models
from squid.search.infrastructure import models as search_models
from squid.settings.infrastructure import models as setting_models
from squid.users.infrastructure import models as user_models
from squid.versions.infrastructure import models as version_models
from squid.voting.infrastructure import models as voting_models

__all__ = [
    "build_models",
    "message_models",
    "record_models",
    "search_models",
    "setting_models",
    "user_models",
    "version_models",
    "voting_models",
]
