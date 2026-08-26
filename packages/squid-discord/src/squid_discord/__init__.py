"""Discord Components V2 target, renderer, and runtime adapter."""

# Deeper-tier namespaces: no names of their own promoted to squid_discord's root, reachable
# as squid_discord.<name>.X. One namespace per physical module (modal.py -> modals, to avoid
# reading like sl.forms; navigation_controls named apart from discord.navigation because
# they are two distinct source modules that would otherwise collide on one attribute).
from importlib import import_module

from squid_discord import (
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
    host,
    inspection,
    live,
    mount,
    navigation,
    presentation,
    renderer,
    roles,
    routing,
    screens,
    sessions,
    target,
    targets,
    testing,
)
from squid_discord import (
    modal as modals,
)
from squid_discord.access import (
    AccessPolicy,
    Everyone,
    Owner,
    Users,
)
from squid_discord.actions import (
    native,
    responder,
)
from squid_discord.adoption import (
    AdoptionError,
    adopt,
)
from squid_discord.challenges import ChallengeRunner, DialogPresenter
from squid_discord.composition import (
    Composition,
    compose,
    render_item,
    render_static,
)
from squid_discord.conformance import conform
from squid_discord.defaults import (
    MountDefaults,
    MountOptions,
)
from squid_discord.delivery import (
    Destination,
    deliver_to,
    edit_to,
    reply_to,
    respond_to,
    send_to,
)
from squid_discord.fragments import contribute
from squid_discord.grids import button_grid
from squid_discord.host import (
    LayoutHost,
    LayoutHostMissing,
    install,
)
from squid_discord.live import mounts
from squid_discord.managed import (
    ErrorObserver,
    ErrorRenderer,
    ManagedDelivery,
    ManagedError,
    MountFactory,
    SuccessRenderer,
    Work,
    run_managed_result,
)
from squid_discord.mount import (
    ChallengePresenter,
    ChallengeRequest,
    ChallengeSupervisor,
    Mount,
    PauseUpdates,
    RenewEphemeral,
    owned_mount,
)
from squid_discord.navigation import Navigator
from squid_discord.presentation import (
    DiscordMode,
    DiscordModeError,
    DiscordPresentation,
    mode_of,
)
from squid_discord.roles import (
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
from squid_discord.scheduler import (
    MountScheduler,
    MountSchedulerSnapshot,
)
from squid_discord.screens import Opener, Scope, ScreenOptionsResolver, ScreenSpec
from squid_discord.sessions import (
    SessionKey,
    SessionRegistry,
)
from squid_discord.target import (
    DISCORD_V1_DPY27,
    DISCORD_V2_DPY27,
)

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

_LAZY_NAMESPACES = frozenset({"durability"})
"""Namespaces imported on first use rather than by the bundle above.

`durability` is the only part of this package that reaches outside its base dependencies: it
is built on `squid_stores`, which arrives with the `durable` extra. Importing it eagerly would
make `import squid_discord` fail on an install that never persists a panel. It stays in
`__all__` because it is documented surface, and `from squid_discord import durability` still
works -- the import system consults this hook before falling back to the submodule.
`devtools_runtime`, `devtools` and `devtools_view` name its types under `TYPE_CHECKING` for the same
reason; every use there is an annotation.
"""


def __getattr__(name: str) -> object:
    if name in _LAZY_NAMESPACES:
        module = import_module(f"squid_discord.{name}")
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
    "Composition",
    "Destination",
    "DialogPresenter",
    "DiscordMode",
    "DiscordModeError",
    "DiscordPresentation",
    "ErrorObserver",
    "ErrorRenderer",
    "Everyone",
    "ExistingLayoutError",
    "LayoutHost",
    "LayoutHostMissing",
    "LimitViolationError",
    "ManagedDelivery",
    "ManagedError",
    "Mount",
    "MountDefaults",
    "MountFactory",
    "MountOptions",
    "MountScheduler",
    "MountSchedulerSnapshot",
    "Navigator",
    "Opener",
    "Owner",
    "PauseUpdates",
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
    "ScreenOptionsResolver",
    "ScreenSpec",
    "Scope",
    "SessionKey",
    "SessionRegistry",
    "SuccessRenderer",
    "Users",
    "Work",
    "access",
    "actions",
    "adapter",
    "adopt",
    "button_grid",
    "challenges",
    "classic",
    "classic_renderer",
    "compose",
    "conform",
    "conformance",
    "contribute",
    "deliver_to",
    "delivery",
    "devtools",
    "devtools_runtime",
    "durability",
    "edit_to",
    "fragments",
    "grids",
    "guards",
    "host",
    "inspection",
    "install",
    "limits",
    "live",
    "modals",
    "mode_of",
    "mount",
    "mounts",
    "native",
    "navigation",
    "navigation_controls",
    "owned_mount",
    "presentation",
    "render_item",
    "render_static",
    "renderer",
    "reply_to",
    "respond_to",
    "responder",
    "roles",
    "routing",
    "run_managed_result",
    "screens",
    "send_to",
    "sessions",
    "target",
    "targets",
    "testing",
]
