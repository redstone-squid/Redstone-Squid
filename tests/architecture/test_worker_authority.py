"""Queue ownership and worker-only writes require a named worker at entry."""

from inspect import Parameter, signature
from unittest.mock import Mock

import pytest

from squid.core.actors import ActingContext, Actor, AnonymousActor, ServiceActor, UserActor
from squid.core.builds.application import BuildService
from squid.core.errors import AuthorizationError
from squid.core.events.application import DomainEventService
from squid.core.media.application.jobs import MediaProcessingJobService
from squid.core.schematics.application.jobs import SchematicJobService
from squid.core.schematics.application.render_jobs import SchematicRenderJobService
from squid.core.voting.application import VoteService

WORKER_OPERATIONS = {
    DomainEventService: ("claim", "complete", "fail", "reject"),
    SchematicRenderJobService: ("claim", "complete", "fail"),
    SchematicJobService: ("claim", "complete", "fail", "renew"),
    MediaProcessingJobService: ("claim", "complete", "fail", "heartbeat"),
    BuildService: ("save", "edit", "apply_reviewed_edit", "clean_stale_locks"),
    VoteService: ("start_build_vote", "ensure_build_submission_vote"),
}


@pytest.mark.parametrize(
    "actor", [AnonymousActor(), UserActor(account_id=1, is_bot_owner=True), ServiceActor(service_id="indexer")]
)
async def test_worker_operations_reject_other_principal_kinds_before_io(actor: Actor) -> None:
    for service, names in WORKER_OPERATIONS.items():
        for name in names:
            operation = getattr(service, name)
            parameters = signature(operation).parameters
            context = parameters["context"]
            assert context.kind is Parameter.KEYWORD_ONLY
            assert context.default is Parameter.empty
            arguments = {
                key: Mock()
                for key, value in parameters.items()
                if key not in {"self", "context"} and value.default is Parameter.empty
            }
            with pytest.raises(AuthorizationError):
                await operation(Mock(), context=ActingContext.direct(actor), **arguments)
