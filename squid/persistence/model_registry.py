"""Import every SQLAlchemy model for migration metadata registration."""

from squid.accounts import models as account_models
from squid.auth.models import entities as auth_models
from squid.auth.models import sessions as auth_session_models
from squid.builds import history_models as build_history_models
from squid.builds import models as build_models
from squid.cli_auth import models as cli_auth_models
from squid.diagnostics import models as diagnostic_models
from squid.edits import models as edit_models
from squid.events import models as event_models
from squid.idempotency import models as idempotency_models
from squid.media import models as media_models
from squid.messages import models as message_models
from squid.minecraft_auth import models as minecraft_auth_models
from squid.notifications import models as notification_models
from squid.permissions import models as permission_models
from squid.posts import models as post_models
from squid.records import models as record_models
from squid.schematics import models as schematic_models
from squid.search import models as search_models
from squid.settings import models as setting_models
from squid.starboard import models as starboard_models
from squid.submissions.models import entities as submission_models
from squid.submissions.models import finalization as submission_finalization_models
from squid.submissions.models import intake as submission_intake_models
from squid.submissions.models import revisions as submission_revision_models
from squid.submissions.models import schematics as submission_schematic_models
from squid.sync import models as sync_models
from squid.tags import models as tag_models
from squid.versions import models as version_models
from squid.voting import models as voting_models

__all__ = [
    "account_models",
    "auth_models",
    "auth_session_models",
    "build_history_models",
    "build_models",
    "cli_auth_models",
    "diagnostic_models",
    "edit_models",
    "event_models",
    "idempotency_models",
    "media_models",
    "message_models",
    "minecraft_auth_models",
    "notification_models",
    "permission_models",
    "post_models",
    "record_models",
    "schematic_models",
    "search_models",
    "setting_models",
    "starboard_models",
    "submission_finalization_models",
    "submission_inference_models",
    "submission_intake_models",
    "submission_models",
    "submission_revision_models",
    "submission_schematic_models",
    "sync_models",
    "tag_models",
    "version_models",
    "voting_models",
]

from squid.submissions.models import inference as submission_inference_models
