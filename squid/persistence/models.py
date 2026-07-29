"""Import every context-owned ORM model for metadata registration."""

from squid.builds.infrastructure import models as build_models
from squid.messages.infrastructure import models as message_models
from squid.settings.infrastructure import models as setting_models
from squid.users.infrastructure import models as user_models
from squid.versions.infrastructure import models as version_models
from squid.voting.infrastructure import models as voting_models

__all__ = [
    "build_models",
    "message_models",
    "setting_models",
    "user_models",
    "version_models",
    "voting_models",
]


def load_models() -> None:
    """Load all model modules into shared metadata at composition boundaries."""
