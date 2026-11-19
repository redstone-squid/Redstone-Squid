"""Import every SQLAlchemy model for migration metadata registration."""

from squid.core.accounts import models as account_models
from squid.core.auth.models import entities as auth_models
from squid.core.auth.models import sessions as auth_session_models
from squid.core.builds import history_models as build_history_models
from squid.core.builds import models as build_models
from squid.core.cli_auth import models as cli_auth_models
from squid.core.diagnostics import models as diagnostic_models
from squid.core.edits import models as edit_models
from squid.core.events import models as event_models
from squid.core.idempotency import models as idempotency_models
from squid.core.media import models as media_models
from squid.core.messages import models as message_models
from squid.core.minecraft_auth import models as minecraft_auth_models
from squid.core.notifications import models as notification_models
from squid.core.permissions import models as permission_models
from squid.core.posts import models as post_models
from squid.core.records import models as record_models
from squid.core.schematics import models as schematic_models
from squid.core.search import models as search_models
from squid.core.settings import models as setting_models
from squid.core.starboard import models as starboard_models
from squid.core.submissions.models import entities as submission_models
from squid.core.submissions.models import finalization as submission_finalization_models
from squid.core.submissions.models import intake as submission_intake_models
from squid.core.submissions.models import recalculation as submission_recalculation_models
from squid.core.submissions.models import schematics as submission_schematic_models
from squid.core.sync import models as sync_models
from squid.core.tags import models as tag_models
from squid.core.versions import models as version_models
from squid.core.voting import models as voting_models

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
    "submission_recalculation_models",
    "submission_schematic_models",
    "sync_models",
    "tag_models",
    "version_models",
    "voting_models",
]

from squid.core.submissions.models import inference as submission_inference_models
