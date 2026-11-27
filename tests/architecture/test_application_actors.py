"""Process service bundles define the application operations available to entry points."""

from functools import cached_property
from inspect import Parameter, getmembers, isclass, isroutine, signature
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from squid.core.actors import ActingContext
from squid.core.submissions.bootstrap import SubmissionServices
from squid.runtime import ApiServices, BotServices, WorkerServices

# These calls establish identity before an invocation or look up other principals.
IDENTITY_RESOLUTION = {
    "AccountService": {"get_account_by_id", "get_account_by_identity", "get_accounts", "get_or_create_identity"},
    "ApiKeyService": {"authenticate"},
    "WebSessionService": {"authenticate"},
    "CliAuthorizationService": {
        "authenticate",
        "exchange_enrollment",
        "exchange_session_challenge",
        "start_enrollment",
        "start_session_challenge",
    },
    "InstallationCredentialService": {"authenticate", "authenticate_headers"},
    "PlayerAuthorizationService": {
        "authenticate_fabric_player",
        "authenticate_paper_player",
        "exchange_fabric",
        "exchange_paper",
        "start_fabric_challenge",
        "start_paper_challenge",
    },
    "InteractiveVoteActorResolver": {"member", "resolve"},
}

# Policy calculations can evaluate a voter, grantee, or proposed role independently of the caller.
POLICY_QUERIES = {
    "PermissionService": {"check", "require"},
    "PermissionAdministrationService": {"check_assignment", "check_include", "check_revoke", "check_unassignment"},
}

# Resource lifecycle, storage and Discord I/O are process dependencies.
INFRASTRUCTURE = {
    "CheckedInFormManifestRegistry": {"get", "current", "edit"},  # Immutable schemas shared across readers.
    "PermissionEpochWatcher": {"listen", "refresh"},
    "SchematicService": {"aclose"},
    "VoteCardReactions": {"add_own", "read", "remove"},
    "ArtifactStore": {"aclose", "delete", "get", "get_path", "put", "put_path", "stat"},
    "BallotRecounter": {"count_cards"},  # Recount callback uses the enclosing operation's invocation.
    "DomainEventWakeListener": {"run"},
    "MediaProcessingJobService": {"download"},  # Copies an artifact the caller already authorized.
}


# Synchronous calculations and process wiring do not perform work for an actor.
CONTEXT_FREE_OPERATIONS = {
    "ApiKeyService": {"hash_secret"},
    "ErrorReportService": {"normalize"},
    "PermissionAdministrationService": {
        "check_grant",
        "check_pattern",
        "check_rank",
        "check_role_creation",
        "resolved_leaves",
        "unmanageable_patterns",
        "in_transaction",
    },
    "RecordComputationService": {
        "compute_snapshot",
        "door_range_candidates",
        "format_record_titles",
        "requested_category",
        "scope_categories",
    },
    "SchematicService": {"render_recipe"},
    "SubmissionAttachmentIntake": {"max_file_bytes"},
    "SubmissionDraftService": {"progress"},  # Counts answers on a draft the caller already loaded.
    "VoteService": {"set_actor_resolver"},  # Wire the process's Discord or worker resolver.
    "WebSessionService": {"hash_token"},
}


def test_process_application_operations_require_context() -> None:
    exemptions = IDENTITY_RESOLUTION | POLICY_QUERIES | INFRASTRUCTURE
    services = set()
    for bundle in (ApiServices, BotServices, WorkerServices, SubmissionServices):
        types = list(get_type_hints(bundle).values())
        types.extend(
            get_type_hints(prop.func)["return"] for prop in vars(bundle).values() if isinstance(prop, cached_property)
        )
        for hint in types:
            for service in get_args(hint) if get_origin(hint) is UnionType else (hint,):
                if isclass(service) and service.__module__.startswith("squid."):
                    services.add(service)
    failures = []
    for service in services:
        for name, operation in getmembers(service, isroutine):
            if (
                name.startswith("_")
                or name.endswith("_in")
                or name in exemptions.get(service.__name__, ())
                or name in CONTEXT_FREE_OPERATIONS.get(service.__name__, ())
            ):
                continue
            parameter = signature(operation).parameters.get("context")
            if (
                parameter is None
                or parameter.kind != Parameter.KEYWORD_ONLY
                or parameter.default is not Parameter.empty
                or get_type_hints(operation).get("context") is not ActingContext
            ):
                failures.append(f"{service.__name__}.{name}")
    assert not failures, sorted(failures)
