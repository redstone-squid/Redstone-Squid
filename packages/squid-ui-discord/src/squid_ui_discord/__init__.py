"""Discord Components V2 target, renderer, and runtime adapter."""

# Deeper-tier namespaces: no names of their own promoted to squid_ui_discord's root, reachable
# as squid_ui_discord.<name>.X. One namespace per physical module (modal.py -> modals, to avoid
# reading like sl.forms; navigation_controls named apart from discord.navigation because
# they are two distinct source modules that would otherwise collide on one attribute).
from importlib import import_module

# `target.v2()` and `target.classic()` are deliberately not promoted here: `classic` at this
# level already names the classic-composition submodule, and one name may not mean two things.
from squid_ui.errors import (
    ExistingLayoutError,
    LimitViolationError,
)
from squid_ui.planning import limits as limits
from squid_ui.planning import navigation as navigation_controls
from squid_ui.planning.limits import LIMITS as V2_LIMITS
from squid_ui.planning.planner import EMPTY_RESERVATION
from squid_ui.planning.target import ResourceCost
from squid_ui_discord import (
    access,
    actions,
    adapter,
    challenges,
    classic,
    classic_renderer,
    conformance,
    delivery,
    devtools,
    devtools_runtime,
    fragments,
    grids,
    guards,
    inspection,
    invocation,
    live,
    message_payload,
    message_root,
    navigation,
    renderer,
    rendering,
    roles,
    routing,
    runtime,
    session_specs,
    sessions,
    target,
    targets,
    testing,
)
from squid_ui_discord import (
    modal as modals,
)
from squid_ui_discord.access import (
    AccessPolicy,
    Everyone,
    Owner,
    Users,
)
from squid_ui_discord.actions import (
    native,
    responder,
)
from squid_ui_discord.adoption import (
    AdoptionError,
    adopt,
)
from squid_ui_discord.challenges import ChallengeRunner, DialogPresenter
from squid_ui_discord.conformance import conform
from squid_ui_discord.delivery import (
    MessageDestination,
    deliver_to,
    edit_to,
    reply_to,
    respond_to,
    send_to,
)
from squid_ui_discord.fragments import contribute
from squid_ui_discord.grids import button_grid
from squid_ui_discord.invocation import Invocation, Private, Visibility, current_invocation, invocation_scope
from squid_ui_discord.live import message_roots
from squid_ui_discord.managed import (
    ErrorObserver,
    ErrorRenderer,
    ManagedDelivery,
    ManagedError,
    MessageRootFactory,
    SuccessRenderer,
    Work,
    run_managed_result,
)
from squid_ui_discord.message_payload import (
    MessageMode,
    MessageModeError,
    MessagePayload,
    message_mode,
)
from squid_ui_discord.message_root import (
    ChallengePresenter,
    ChallengeRequest,
    ChallengeSupervisor,
    MessageRoot,
    PauseUpdates,
    RenewEphemeral,
    current_message_root,
)
from squid_ui_discord.message_root_options import (
    MessageRootDefaults,
    MessageRootOptions,
)
from squid_ui_discord.message_root_scheduler import (
    MessageRootScheduler,
    MessageRootSchedulerSnapshot,
)
from squid_ui_discord.navigation import StackNavigator
from squid_ui_discord.rendering import (
    RenderedMessage,
    render_item,
    render_message,
    render_static,
)
from squid_ui_discord.roles import (
    ANY,
    AT_LEAST_ONE,
    AT_MOST_ONE,
    EXACTLY_ONE,
    Cardinality,
    RoleCategory,
    RoleConfigurationUnavailable,
    RoleMutationFailed,
    RoleMutationForbidden,
    RoleNoticeHandler,
    RoleOption,
    RolePanel,
    RoleSelectionInvalid,
    RolesUnchanged,
    RolesUpdated,
    RoleTransitionResult,
)
from squid_ui_discord.runtime import (
    ClientRuntime,
    ClientRuntimeMissing,
    InvocationSource,
    LocalizationResolver,
    install,
)
from squid_ui_discord.session_specs import MessageRootOptionsResolver, OpenContext, ScopeKind, SessionSpec
from squid_ui_discord.sessions import (
    SessionKey,
    SessionManager,
)
from squid_ui_discord.target import (
    DISCORD_V1_DPY27,
    DISCORD_V2_DPY27,
)

_LAZY_NAMESPACES = frozenset({"durability"})
"""Namespaces imported on first use rather than by the bundle above.

`durability` is the only part of this package that reaches outside its base dependencies: it
is built on `squid_storage`, which arrives with the `durable` extra. Importing it eagerly would
make `import squid_ui_discord` fail on an install that never persists a panel. It stays in
`__all__` because it is documented surface, and `from squid_ui_discord import durability` still
works -- the import system consults this hook before falling back to the submodule.
`devtools_runtime`, `devtools` and `devtools_view` name its types under `TYPE_CHECKING` for the same
reason; every use there is an annotation.
"""


def __getattr__(name: str) -> object:
    if name in _LAZY_NAMESPACES:
        module = import_module(f"squid_ui_discord.{name}")
        globals()[name] = module  # bind it, so this hook runs at most once per name
        return module
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_NAMESPACES)


__all__ = [
    "ANY",
    "AT_LEAST_ONE",
    "AT_MOST_ONE",
    "DISCORD_V1_DPY27",
    "DISCORD_V2_DPY27",
    "EMPTY_RESERVATION",
    "EXACTLY_ONE",
    "V2_LIMITS",
    "AccessPolicy",
    "AdoptionError",
    "Cardinality",
    "ChallengePresenter",
    "ChallengeRequest",
    "ChallengeRunner",
    "ChallengeSupervisor",
    "ClientRuntime",
    "ClientRuntimeMissing",
    "DialogPresenter",
    "ErrorObserver",
    "ErrorRenderer",
    "Everyone",
    "ExistingLayoutError",
    "Invocation",
    "InvocationSource",
    "LimitViolationError",
    "LocalizationResolver",
    "ManagedDelivery",
    "ManagedError",
    "MessageDestination",
    "MessageMode",
    "MessageModeError",
    "MessagePayload",
    "MessageRoot",
    "MessageRootDefaults",
    "MessageRootFactory",
    "MessageRootOptions",
    "MessageRootOptionsResolver",
    "MessageRootScheduler",
    "MessageRootSchedulerSnapshot",
    "OpenContext",
    "Owner",
    "PauseUpdates",
    "Private",
    "RenderedMessage",
    "RenewEphemeral",
    "ResourceCost",
    "RoleCategory",
    "RoleConfigurationUnavailable",
    "RoleMutationFailed",
    "RoleMutationForbidden",
    "RoleNoticeHandler",
    "RoleOption",
    "RolePanel",
    "RoleSelectionInvalid",
    "RoleTransitionResult",
    "RolesUnchanged",
    "RolesUpdated",
    "ScopeKind",
    "SessionKey",
    "SessionManager",
    "SessionSpec",
    "StackNavigator",
    "SuccessRenderer",
    "Users",
    "Visibility",
    "Work",
    "access",
    "actions",
    "adapter",
    "adopt",
    "button_grid",
    "challenges",
    "classic",
    "classic_renderer",
    "conform",
    "conformance",
    "contribute",
    "current_invocation",
    "current_message_root",
    "deliver_to",
    "delivery",
    "devtools",
    "devtools_runtime",
    "durability",
    "edit_to",
    "fragments",
    "grids",
    "guards",
    "inspection",
    "install",
    "invocation",
    "invocation_scope",
    "limits",
    "live",
    "message_mode",
    "message_payload",
    "message_root",
    "message_roots",
    "modals",
    "native",
    "navigation",
    "navigation_controls",
    "render_item",
    "render_message",
    "render_static",
    "renderer",
    "rendering",
    "reply_to",
    "respond_to",
    "responder",
    "roles",
    "routing",
    "run_managed_result",
    "runtime",
    "send_to",
    "session_specs",
    "sessions",
    "target",
    "targets",
    "testing",
]
