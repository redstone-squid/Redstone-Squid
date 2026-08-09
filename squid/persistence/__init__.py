"""Shared SQLAlchemy infrastructure and model registration."""

from squid.auth.infrastructure import models as auth_models
from squid.auth.infrastructure import session_models as auth_session_models
from squid.builds.infrastructure import models as build_models
from squid.events.infrastructure import models as event_models
from squid.messages.infrastructure import models as message_models
from squid.permissions.infrastructure import models as permission_models
from squid.records.infrastructure import models as record_models
from squid.schematics.infrastructure import models as schematic_models
from squid.search.infrastructure import models as search_models
from squid.settings.infrastructure import models as setting_models
from squid.starboard.infrastructure import models as starboard_models
from squid.sync.infrastructure import models as sync_models
from squid.tags.infrastructure import models as tag_models
from squid.users.infrastructure import models as user_models
from squid.versions.infrastructure import models as version_models
from squid.voting.infrastructure import models as voting_models

__all__ = [
    "auth_models",
    "auth_session_models",
    "build_models",
    "event_models",
    "message_models",
    "permission_models",
    "record_models",
    "schematic_models",
    "search_models",
    "setting_models",
    "starboard_models",
    "sync_models",
    "tag_models",
    "user_models",
    "version_models",
    "voting_models",
]
