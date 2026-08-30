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
    action,
    actions,
    adapter,
    audience,
    challenges,
    classic,
    classic_renderer,
    config,
    conformance,
    contracts,
    delivery,
    devtools,
    devtools_runtime,
    facade,
    fragments,
    grids,
    guards,
    inspection,
    live,
    message_payload,
    message_root,
    navigation,
    renderer,
    rendering,
    request,
    response,
    roles,
    routing,
    runtime,
    screen,
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
from squid_ui_discord.action import DiscordAction
from squid_ui_discord.actions import (
    native,
    responder,
)
from squid_ui_discord.adoption import (
    AdoptionError,
    adopt,
)
from squid_ui_discord.audience import Audience, Private, Visibility
from squid_ui_discord.challenges import ChallengeRunner, DialogPresenter
from squid_ui_discord.config import DiscordUIConfig
from squid_ui_discord.conformance import conform
from squid_ui_discord.contracts import LocalizationResolver, RequestSource, ResponseSource
from squid_ui_discord.delivery import (
    MessageDestination,
    deliver_to,
    edit_to,
    reply_to,
    respond_to,
    send_to,
)
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.fragments import contribute
from squid_ui_discord.grids import button_grid
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
    MessageRoot,
    owner_message_root,
)
from squid_ui_discord.message_root_contracts import (
    DEFAULT_MESSAGE_ROOT_CONFIG,
    ChallengePresenter,
    ChallengeRequest,
    ChallengeSupervisor,
    MessageRootConfig,
    PauseUpdates,
    RenewEphemeral,
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
from squid_ui_discord.request import DiscordRequest
from squid_ui_discord.response import (
    Abandoned,
    Presented,
    Rejected,
    Response,
    ResponseResult,
    ResponseSpec,
    Sent,
    invoker_only,
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
    DiscordUIRuntime,
    DiscordUIRuntimeMissing,
    install,
)
from squid_ui_discord.screen import Screen
from squid_ui_discord.session_specs import OpenContext, ScopeKind, SessionOptions, SessionOptionsResolver, SessionSpec
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
    "DEFAULT_MESSAGE_ROOT_CONFIG",
    "DISCORD_V1_DPY27",
    "DISCORD_V2_DPY27",
    "EMPTY_RESERVATION",
    "EXACTLY_ONE",
    "V2_LIMITS",
    "Abandoned",
    "AccessPolicy",
    "AdoptionError",
    "Audience",
    "Cardinality",
    "ChallengePresenter",
    "ChallengeRequest",
    "ChallengeRunner",
    "ChallengeSupervisor",
    "DialogPresenter",
    "DiscordAction",
    "DiscordRequest",
    "DiscordUI",
    "DiscordUIConfig",
    "DiscordUIRuntime",
    "DiscordUIRuntimeMissing",
    "ErrorObserver",
    "ErrorRenderer",
    "Everyone",
    "ExistingLayoutError",
    "LimitViolationError",
    "LocalizationResolver",
    "ManagedDelivery",
    "ManagedError",
    "MessageDestination",
    "MessageMode",
    "MessageModeError",
    "MessagePayload",
    "MessageRoot",
    "MessageRootConfig",
    "MessageRootDefaults",
    "MessageRootFactory",
    "MessageRootOptions",
    "MessageRootScheduler",
    "MessageRootSchedulerSnapshot",
    "OpenContext",
    "Owner",
    "PauseUpdates",
    "Presented",
    "Private",
    "RequestSource",
    "Rejected",
    "RenderedMessage",
    "RenewEphemeral",
    "ResourceCost",
    "Response",
    "ResponseResult",
    "ResponseSpec",
    "ResponseSource",
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
    "Screen",
    "Sent",
    "SessionKey",
    "SessionManager",
    "SessionOptions",
    "SessionOptionsResolver",
    "SessionSpec",
    "StackNavigator",
    "SuccessRenderer",
    "Users",
    "Visibility",
    "Work",
    "access",
    "action",
    "actions",
    "adapter",
    "adopt",
    "audience",
    "button_grid",
    "challenges",
    "classic",
    "classic_renderer",
    "config",
    "conform",
    "conformance",
    "contracts",
    "contribute",
    "deliver_to",
    "delivery",
    "devtools",
    "devtools_runtime",
    "durability",
    "edit_to",
    "facade",
    "fragments",
    "grids",
    "guards",
    "inspection",
    "install",
    "invoker_only",
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
    "owner_message_root",
    "render_item",
    "render_message",
    "render_static",
    "renderer",
    "rendering",
    "reply_to",
    "request",
    "respond_to",
    "responder",
    "response",
    "roles",
    "routing",
    "run_managed_result",
    "runtime",
    "screen",
    "send_to",
    "session_specs",
    "sessions",
    "target",
    "targets",
    "testing",
]
