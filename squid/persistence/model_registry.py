"""Import every SQLAlchemy model for migration metadata registration."""

from squid.accounts.infrastructure import models as account_models
from squid.auth.infrastructure import models as auth_models
from squid.auth.infrastructure import session_models as auth_session_models
from squid.builds.infrastructure import models as build_models
from squid.events.infrastructure import models as event_models
from squid.idempotency.infrastructure import models as idempotency_models
from squid.media.infrastructure import models as media_models
from squid.messages.infrastructure import models as message_models
from squid.minecraft_auth.infrastructure import models as minecraft_auth_models
from squid.notifications.infrastructure import models as notification_models
from squid.permissions.infrastructure import models as permission_models
from squid.records.infrastructure import models as record_models
from squid.schematics.infrastructure import models as schematic_models
from squid.search.infrastructure import models as search_models
from squid.settings.infrastructure import models as setting_models
from squid.starboard.infrastructure import models as starboard_models
from squid.submissions.infrastructure import finalization_models as submission_finalization_models
from squid.submissions.infrastructure import models as submission_models
from squid.sync.infrastructure import models as sync_models
from squid.tags.infrastructure import models as tag_models
from squid.versions.infrastructure import models as version_models
from squid.voting.infrastructure import models as voting_models

__all__ = [
    "account_models",
    "auth_models",
    "auth_session_models",
    "build_models",
    "event_models",
    "idempotency_models",
    "media_models",
    "message_models",
    "minecraft_auth_models",
    "notification_models",
    "permission_models",
    "record_models",
    "schematic_models",
    "search_models",
    "setting_models",
    "starboard_models",
    "submission_finalization_models",
    "submission_models",
    "sync_models",
    "tag_models",
    "version_models",
    "voting_models",
]
