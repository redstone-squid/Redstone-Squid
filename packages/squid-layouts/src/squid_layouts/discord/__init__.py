"""Discord Components V2 target, renderer, and runtime adapter."""

# Deeper-tier namespaces: no names of their own promoted to sl.discord's root, reachable
# as sl.discord.<name>.X. One namespace per physical module (modal.py -> modals, to avoid
# reading like sl.forms; navigation_controls named apart from discord.navigation because
# they are two distinct source modules that would otherwise collide on one attribute).
from squid_layouts.discord import (
    access,
    actions,
    adapter,
    classic,
    classic_renderer,
    conformance,
    delivery,
    devtools,
    durability,
    fragments,
    guards,
    inspection,
    live,
    mount,
    navigation,
    operations,
    presentation,
    renderer,
    roles,
    routing,
    screens,
    sessions,
    target,
    targets,
)
from squid_layouts.discord import (
    modal as modals,
)
from squid_layouts.discord.access import (
    AccessPolicy,
    Everyone,
    Owner,
    Users,
)
from squid_layouts.discord.actions import (
    native,
    responder,
)
from squid_layouts.discord.adoption import (
    AdoptionError,
    adopt,
)
from squid_layouts.discord.composition import (
    Composition,
    compose,
    render_static,
)
from squid_layouts.discord.conformance import conform
from squid_layouts.discord.defaults import (
    MountDefaults,
    MountOptions,
)
from squid_layouts.discord.delivery import (
    Destination,
    reply_to,
    respond_to,
)
from squid_layouts.discord.fragments import contribute
from squid_layouts.discord.live import mounts
from squid_layouts.discord.mount import (
    Mount,
    PauseUpdates,
    RenewEphemeral,
    owned_mount,
)
from squid_layouts.discord.presentation import (
    DiscordMode,
    DiscordModeError,
    DiscordPresentation,
    mode_of,
)
from squid_layouts.discord.reactor import (
    Reactor,
    ReactorSnapshot,
)
from squid_layouts.discord.roles import (
    ANY,
    AT_LEAST_ONE,
    AT_MOST_ONE,
    EXACTLY_ONE,
    Cardinality,
    RoleCategory,
    RoleConfigurationUnavailable,
    RoleFeedback,
    RoleMutationFailed,
    RoleMutationForbidden,
    RoleOption,
    RolePanel,
    RoleSelectionInvalid,
    RolesUnchanged,
    RolesUpdated,
    RoleTransitionResult,
)
from squid_layouts.discord.screens import Screen
from squid_layouts.discord.sessions import (
    SessionKey,
    SessionRegistry,
)
from squid_layouts.discord.target import (
    CLASSIC_TARGET,
    V2_TARGET,
    Target,
)
from squid_layouts.errors import (
    ExistingLayoutError,
    LimitViolationError,
)
from squid_layouts.planning import limits as limits
from squid_layouts.planning import navigation as navigation_controls
from squid_layouts.planning.limits import LIMITS as V2_LIMITS
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.target import ResourceCost

__all__ = [
    "ANY",
    "AT_LEAST_ONE",
    "AT_MOST_ONE",
    "CLASSIC_TARGET",
    "EMPTY_RESERVATION",
    "EXACTLY_ONE",
    "V2_LIMITS",
    "V2_TARGET",
    "AccessPolicy",
    "AdoptionError",
    "Cardinality",
    "Composition",
    "Destination",
    "DiscordMode",
    "DiscordModeError",
    "DiscordPresentation",
    "Everyone",
    "ExistingLayoutError",
    "LimitViolationError",
    "Mount",
    "MountDefaults",
    "MountOptions",
    "Owner",
    "PauseUpdates",
    "Reactor",
    "ReactorSnapshot",
    "RenewEphemeral",
    "ResourceCost",
    "RoleCategory",
    "RoleConfigurationUnavailable",
    "RoleFeedback",
    "RoleMutationFailed",
    "RoleMutationForbidden",
    "RoleOption",
    "RolePanel",
    "RoleSelectionInvalid",
    "RoleTransitionResult",
    "RolesUnchanged",
    "RolesUpdated",
    "Screen",
    "SessionKey",
    "SessionRegistry",
    "Target",
    "Users",
    "access",
    "actions",
    "adapter",
    "adopt",
    "classic",
    "classic_renderer",
    "compose",
    "conform",
    "conformance",
    "contribute",
    "delivery",
    "devtools",
    "durability",
    "fragments",
    "guards",
    "inspection",
    "limits",
    "live",
    "modals",
    "mode_of",
    "mount",
    "mounts",
    "native",
    "navigation",
    "navigation_controls",
    "operations",
    "owned_mount",
    "presentation",
    "render_static",
    "renderer",
    "reply_to",
    "respond_to",
    "responder",
    "roles",
    "routing",
    "screens",
    "sessions",
    "target",
    "targets",
]
