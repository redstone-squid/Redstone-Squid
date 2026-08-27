"""Persistent, router-owned self-role panels for Discord."""

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import discord

from squid_ui.emoji import EmojiLike, normalize_emoji
from squid_ui.primitives.nodes import (
    ControlGroup,
    Heading,
    Option,
    RoutedButton,
    RoutedSelect,
    Text,
    Variant,
    Variants,
)
from squid_ui.routing import Route
from squid_ui.runtime.component import Component
from squid_ui.semantic import LayoutNode
from squid_ui.text import TextLike
from squid_ui_discord.routing import RouteComponent, RouteGroup


@dataclass(frozen=True, slots=True)
class Cardinality:
    """The minimum and maximum number of roles a category may contain."""

    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        if type(self.minimum) is not int or self.minimum < 0:
            message = "Cardinality.minimum must be a non-negative integer"
            raise ValueError(message)
        if self.maximum is not None and (type(self.maximum) is not int or self.maximum < 0):
            message = "Cardinality.maximum must be a non-negative integer or None"
            raise ValueError(message)
        if self.maximum is not None and self.minimum > self.maximum:
            message = "Cardinality.minimum must not exceed maximum"
            raise ValueError(message)


ANY = Cardinality()
"""Any number of roles in the category, including none."""

AT_MOST_ONE = Cardinality(maximum=1)
"""Zero or one role in the category."""

AT_LEAST_ONE = Cardinality(minimum=1)
"""One or more roles in the category."""

EXACTLY_ONE = Cardinality(minimum=1, maximum=1)
"""Exactly one role in the category."""


@dataclass(frozen=True, slots=True)
class RoleOption:
    """One Discord role offered by a :class:`RoleCategory`."""

    role_id: int
    label: TextLike
    emoji: EmojiLike | None = None
    description: TextLike | None = None

    def __post_init__(self) -> None:
        if type(self.role_id) is not int or self.role_id <= 0:
            message = "RoleOption.role_id must be a positive integer"
            raise ValueError(message)
        object.__setattr__(self, "emoji", normalize_emoji(self.emoji))


@dataclass(frozen=True, slots=True)
class RoleCategory:
    """A named group of roles and the cardinality it permits."""

    key: str
    label: TextLike
    roles: tuple[RoleOption, ...]
    cardinality: Cardinality = ANY
    description: TextLike | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key or ":" in self.key:
            message = "RoleCategory.key must be a non-empty route-safe string"
            raise ValueError(message)
        roles = tuple(self.roles)
        if not 1 <= len(roles) <= 25:
            message = "RoleCategory.roles must contain between 1 and 25 options"
            raise ValueError(message)
        role_ids = [role.role_id for role in roles]
        if len(set(role_ids)) != len(role_ids):
            message = f"RoleCategory {self.key!r} contains duplicate role ids"
            raise ValueError(message)
        maximum = len(roles) if self.cardinality.maximum is None else self.cardinality.maximum
        if self.cardinality.minimum > maximum:
            message = f"RoleCategory {self.key!r} has an impossible minimum"
            raise ValueError(message)
        if maximum > len(roles):
            message = f"RoleCategory {self.key!r} has a maximum larger than its role count"
            raise ValueError(message)
        object.__setattr__(self, "roles", roles)


def _role_ids(value: Sequence[int] | frozenset[int]) -> frozenset[int]:
    """Freeze role-id collections stored in transition outcomes."""
    return frozenset(value)


@dataclass(frozen=True, slots=True)
class RolesUpdated:
    """A category changed from ``before`` to ``after``."""

    category: str
    before: frozenset[int]
    after: frozenset[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "before", _role_ids(self.before))
        object.__setattr__(self, "after", _role_ids(self.after))

    @property
    def added(self) -> frozenset[int]:
        """Role ids added by the transition."""
        return self.after - self.before

    @property
    def removed(self) -> frozenset[int]:
        """Role ids removed by the transition."""
        return self.before - self.after

    @property
    def current(self) -> frozenset[int]:
        """The role ids held before the transition."""
        return self.before

    @property
    def candidate(self) -> frozenset[int]:
        """The role ids requested by the transition."""
        return self.after


@dataclass(frozen=True, slots=True)
class RolesUnchanged:
    """A request that already matches the member's current category roles."""

    category: str
    roles: frozenset[int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", _role_ids(self.roles))

    @property
    def before(self) -> frozenset[int]:
        """The role ids held before the request."""
        return self.roles

    @property
    def after(self) -> frozenset[int]:
        """The role ids requested by the request."""
        return self.roles

    @property
    def current(self) -> frozenset[int]:
        """The role ids held by the member."""
        return self.roles

    @property
    def candidate(self) -> frozenset[int]:
        """The role ids requested by the request."""
        return self.roles


@dataclass(frozen=True, slots=True)
class RoleSelectionInvalid:
    """A request that cannot produce a valid category selection."""

    category: str
    current: frozenset[int]
    requested: frozenset[int]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", _role_ids(self.current))
        object.__setattr__(self, "requested", _role_ids(self.requested))

    @property
    def candidate(self) -> frozenset[int]:
        """The candidate role ids considered by validation."""
        return self.requested


@dataclass(frozen=True, slots=True)
class RoleConfigurationUnavailable:
    """The panel cannot resolve its configured roles in the interaction guild."""

    category: str
    current: frozenset[int]
    missing_role_ids: frozenset[int]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", _role_ids(self.current))
        object.__setattr__(self, "missing_role_ids", _role_ids(self.missing_role_ids))


@dataclass(frozen=True, slots=True)
class RoleMutationForbidden:
    """Discord or the bot's role hierarchy forbids a requested mutation."""

    category: str
    current: frozenset[int]
    candidate: frozenset[int]
    role_ids: frozenset[int]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", _role_ids(self.current))
        object.__setattr__(self, "candidate", _role_ids(self.candidate))
        object.__setattr__(self, "role_ids", _role_ids(self.role_ids))


@dataclass(frozen=True, slots=True)
class RoleMutationFailed:
    """Discord failed a complete role mutation for an otherwise valid request."""

    category: str
    current: frozenset[int]
    candidate: frozenset[int]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", _role_ids(self.current))
        object.__setattr__(self, "candidate", _role_ids(self.candidate))


type RoleTransitionResult = (
    RolesUpdated
    | RolesUnchanged
    | RoleSelectionInvalid
    | RoleConfigurationUnavailable
    | RoleMutationForbidden
    | RoleMutationFailed
)
type RoleNoticeHandler = Callable[[discord.Interaction[Any], RoleTransitionResult], Awaitable[None]]


@dataclass(slots=True)
class _MemberLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: int = 0


_MEMBER_LOCKS: dict[tuple[int, int], _MemberLock] = {}
_DECIMAL_ROLE_ID = re.compile(r"[0-9]+")


@asynccontextmanager
async def _member_lock(key: tuple[int, int]):
    """Serialize one member while allowing idle lock entries to be collected."""
    entry = _MEMBER_LOCKS.get(key)
    if entry is None:
        entry = _MemberLock()
        _MEMBER_LOCKS[key] = entry
    entry.waiters += 1
    acquired = False
    try:
        await entry.lock.acquire()
        acquired = True
    finally:
        entry.waiters -= 1
        if not acquired and entry.waiters == 0 and not entry.lock.locked():
            _MEMBER_LOCKS.pop(key, None)
    try:
        yield
    finally:
        entry.lock.release()
        if entry.waiters == 0:
            _MEMBER_LOCKS.pop(key, None)


class RoleLike(Protocol):
    """The parts of a Discord role this module reads.

    A protocol rather than `discord.Role` because the hierarchy rules below are pure functions of
    these four attributes, and stating them keeps the role tests able to supply a stand-in without
    constructing a whole gateway object.
    """

    @property
    def id(self) -> int: ...

    @property
    def position(self) -> int: ...

    @property
    def managed(self) -> bool: ...

    def is_default(self) -> bool: ...


def _role_is_default(role: RoleLike) -> bool:
    """Whether a role is the guild's @everyone role."""
    return role.is_default()


def _role_is_editable(role: RoleLike, guild: Any) -> bool:
    """Apply Discord's role-management and hierarchy rules without mutating anything."""
    if _role_is_default(role) or role.managed:
        return False
    bot = getattr(guild, "me", None)
    permissions = getattr(bot, "guild_permissions", None)
    if bot is None or permissions is None or not bool(getattr(permissions, "manage_roles", False)):
        return False
    top_role = getattr(bot, "top_role", None)
    if top_role is None:
        return False
    try:
        return int(role.position) < int(top_role.position)
    except AttributeError, TypeError, ValueError:
        return False


def _default_notice(result: RoleTransitionResult) -> str:
    """Return a short, mention-free message for the built-in notice hook."""
    match result:
        case RolesUpdated():
            return "Your roles were updated."
        case RolesUnchanged():
            return "Your roles are already up to date."
        case RoleSelectionInvalid():
            return "That role selection is not valid."
        case RoleConfigurationUnavailable():
            return "This role panel is unavailable right now."
        case RoleMutationForbidden():
            return "I do not have permission to change those roles."
        case RoleMutationFailed():
            return "Discord could not update your roles. Please try again."


async def _send_default_notice(interaction: discord.Interaction[Any], result: RoleTransitionResult) -> None:
    """Send built-in private notice, using a follow-up after a deferred interaction."""
    content = _default_notice(result)
    allowed_mentions = discord.AllowedMentions.none()
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, allowed_mentions=allowed_mentions)
    else:
        await interaction.response.send_message(content, ephemeral=True, allowed_mentions=allowed_mentions)


class RolePanel(Component):
    """A stateless, persistent panel for managing member self-roles."""

    def __init__(
        self,
        routes: RouteGroup[discord.Client],
        *,
        title: TextLike,
        categories: Sequence[RoleCategory],
        notice: RoleNoticeHandler | None = None,
        audit_reason: str = "Self-role panel",
    ) -> None:
        if not isinstance(routes, RouteGroup):
            message = "RolePanel.routes must be a RouteGroup"
            raise TypeError(message)
        self.routes = routes
        self.title = title
        self.categories = tuple(categories)
        self.notice = notice
        self.audit_reason = audit_reason
        self._categories = self._validate_categories(self.categories)
        self._toggle_route, self._set_route = self._install_routes(routes)

    @staticmethod
    def _validate_categories(categories: tuple[RoleCategory, ...]) -> dict[str, RoleCategory]:
        """Validate panel-wide category and role uniqueness before touching the route group."""
        by_key: dict[str, RoleCategory] = {}
        role_owners: dict[int, str] = {}
        for category in categories:
            if not isinstance(category, RoleCategory):
                message = "RolePanel.categories must contain RoleCategory values"
                raise TypeError(message)
            if category.key in by_key:
                message = f"RolePanel has duplicate category key {category.key!r}"
                raise ValueError(message)
            by_key[category.key] = category
            for role in category.roles:
                owner = role_owners.get(role.role_id)
                if owner is not None:
                    message = f"role id {role.role_id} appears in both categories {owner!r} and {category.key!r}"
                    raise ValueError(message)
                role_owners[role.role_id] = category.key
        return by_key

    def _install_routes(self, routes: RouteGroup[discord.Client]) -> tuple[Route, Route]:
        """Install both route identities only after all construction checks pass."""
        if routes._frozen:
            message = f"RolePanel route group {routes.prefix!r} is already frozen"
            raise RuntimeError(message)
        if routes._definitions or routes._routes or routes._children:
            message = f"RolePanel requires a dedicated empty route group; {routes.prefix!r} already has routes"
            raise ValueError(message)

        toggle_format = "toggle:{category}:{role_id:int}"
        set_format = "set:{category}"
        candidates = (
            Route(f"{routes.prefix}:{toggle_format}"),
            Route(f"{routes.prefix}:{set_format}"),
        )
        if candidates[0].overlaps(candidates[1]):
            message = "RolePanel route identities overlap"
            raise ValueError(message)
        for category in self.categories:
            for role in category.roles:
                candidates[0].id(category=category.key, role_id=role.role_id)
            candidates[1].id(category=category.key)
        for router in routes._routers:
            for route in candidates:
                router._validate_group_route(routes, route)

        definitions = list(routes._definitions)
        registrations = list(routes._routes)
        try:
            toggle_route = routes.define(toggle_format)
            set_route = routes.define(set_format)
            routes.add(toggle_route, self._handle_toggle)
            routes.add(set_route, self._handle_set, component=RouteComponent.SELECT)
        except Exception:
            routes._definitions[:] = definitions
            routes._routes[:] = registrations
            raise
        return toggle_route, set_route

    def render(self) -> Sequence[LayoutNode]:
        """Render stateless buttons with a planner-owned select fallback."""
        nodes: list[LayoutNode] = [Heading(self.title)]
        for category in self.categories:
            nodes.append(Heading(category.label, level=3))
            if category.description is not None:
                nodes.append(Text(category.description))
            options = tuple(
                Option(
                    label=role.label,
                    value=str(role.role_id),
                    description=role.description,
                    emoji=role.emoji,
                )
                for role in category.roles
            )
            buttons = tuple(
                RoutedButton(
                    label=role.label,
                    route_id=self._toggle_route.id(category=category.key, role_id=role.role_id),
                    emoji=role.emoji,
                )
                for role in category.roles
            )
            maximum = len(category.roles) if category.cardinality.maximum is None else category.cardinality.maximum
            select = RoutedSelect(
                options=options,
                route_id=self._set_route.id(category=category.key),
                placeholder=category.label,
                min_values=category.cardinality.minimum,
                max_values=maximum,
            )
            nodes.append(Variants((Variant((ControlGroup(buttons),)), Variant((select,)))))
        return tuple(nodes)

    async def _handle_toggle(
        self,
        interaction: discord.Interaction[discord.Client],
        category: str,
        role_id: int,
    ) -> None:
        """Handle a stable routed button identity."""
        await self._transition(interaction, category, toggle_role_id=role_id)

    async def _handle_set(
        self,
        interaction: discord.Interaction[discord.Client],
        values: tuple[str, ...],
        category: str,
    ) -> None:
        """Handle a stable routed select identity."""
        await self._transition(interaction, category, selected_values=values)

    async def _transition(
        self,
        interaction: discord.Interaction[Any],
        category_key: str,
        *,
        toggle_role_id: int | None = None,
        selected_values: tuple[str, ...] | None = None,
    ) -> None:
        """Acknowledge and serialize one role transition before reporting its outcome."""
        category = self._categories.get(category_key)
        guild = interaction.guild
        actor = interaction.user
        if guild is None or not isinstance(actor, discord.Member):
            result = RoleConfigurationUnavailable(
                category_key,
                frozenset(),
                frozenset(),
                "Role panels are available only to server members.",
            )
            await self._notice(interaction, result)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        if category is None:
            result = RoleSelectionInvalid(category_key, frozenset(), frozenset(), "Unknown role category.")
            await self._notice(interaction, result)
            return

        key = (int(guild.id), int(actor.id))
        async with _member_lock(key):
            result = await self._transition_locked(
                guild,
                actor,
                category,
                toggle_role_id=toggle_role_id,
                selected_values=selected_values,
            )
        await self._notice(interaction, result)

    async def _transition_locked(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        category: RoleCategory,
        *,
        toggle_role_id: int | None,
        selected_values: tuple[str, ...] | None,
    ) -> RoleTransitionResult:
        """Fetch current Discord state and apply at most one complete member edit."""
        try:
            member = await guild.fetch_member(actor.id)
        except discord.NotFound:
            return RoleConfigurationUnavailable(
                category.key,
                frozenset(),
                frozenset(),
                "Your server membership could not be fetched.",
            )
        except discord.Forbidden:
            return RoleMutationForbidden(
                category.key,
                frozenset(),
                frozenset(),
                frozenset(),
                "Discord refused to fetch the server membership.",
            )
        except discord.HTTPException:
            return RoleMutationFailed(
                category.key,
                frozenset(),
                frozenset(),
                "Discord could not fetch the server membership.",
            )

        configured_ids = {role.role_id for item in self.categories for role in item.roles}
        role_by_id = {role_id: guild.get_role(role_id) for role_id in configured_ids}
        missing = frozenset(role_id for role_id, role in role_by_id.items() if role is None)
        member_roles = tuple(member.roles)
        held_ids = frozenset(int(role.id) for role in member_roles if not _role_is_default(role))
        current = held_ids & {role.role_id for role in category.roles}
        if missing:
            return RoleConfigurationUnavailable(
                category.key,
                current,
                missing,
                "One or more configured roles are missing in this server.",
            )
        # Past the guard above every configured id resolved, but the lookup dict still carries the
        # `None` its `get_role` calls could have returned.
        resolved = {role_id: role for role_id, role in role_by_id.items() if role is not None}

        candidate, reason = self._candidate(category, current, toggle_role_id, selected_values)
        if reason is not None:
            return RoleSelectionInvalid(category.key, current, candidate, reason)
        changed = current ^ candidate
        if not changed:
            return RolesUnchanged(category.key, current)

        forbidden = frozenset(role_id for role_id in changed if not _role_is_editable(resolved[role_id], guild))
        if forbidden:
            return RoleMutationForbidden(
                category.key,
                current,
                candidate,
                forbidden,
                "One or more requested roles cannot be changed by the bot.",
            )

        category_ids = {role.role_id for role in category.roles}
        complete_roles: list[Any] = []
        seen: set[int] = set()
        for role in member_roles:
            role_id = int(role.id)
            if _role_is_default(role) or role_id in category_ids or role_id in seen:
                continue
            complete_roles.append(role)
            seen.add(role_id)
        for role in category.roles:
            if role.role_id in candidate:
                complete_roles.append(role_by_id[role.role_id])
                seen.add(role.role_id)

        try:
            await member.edit(roles=complete_roles, reason=self.audit_reason)
        except discord.Forbidden:
            return RoleMutationForbidden(
                category.key,
                current,
                candidate,
                changed,
                "Discord refused the requested role change.",
            )
        except discord.HTTPException:
            return RoleMutationFailed(
                category.key,
                current,
                candidate,
                "Discord could not apply the requested role change.",
            )
        return RolesUpdated(category.key, current, candidate)

    @staticmethod
    def _candidate(
        category: RoleCategory,
        current: frozenset[int],
        toggle_role_id: int | None,
        selected_values: tuple[str, ...] | None,
    ) -> tuple[frozenset[int], str | None]:
        """Compute a candidate category set and explain invalid requests."""
        role_ids = {role.role_id for role in category.roles}
        maximum = len(category.roles) if category.cardinality.maximum is None else category.cardinality.maximum
        if selected_values is not None:
            selected: list[int] = []
            for value in selected_values:
                if not _DECIMAL_ROLE_ID.fullmatch(value):
                    return frozenset(selected), "Select values must be decimal role ids."
                role_id = int(value)
                if str(role_id) != value:
                    return frozenset(selected), "Select values must use canonical role ids."
                if role_id in selected:
                    return frozenset(selected), "A role may only be selected once."
                if role_id not in role_ids:
                    return frozenset(selected), "The selection contains a role outside this category."
                selected.append(role_id)
            candidate = frozenset(selected)
        else:
            assert toggle_role_id is not None
            if toggle_role_id not in role_ids:
                return frozenset({toggle_role_id}), "The requested role is outside this category."
            if toggle_role_id in current:
                candidate = current - {toggle_role_id}
                if len(candidate) < category.cardinality.minimum:
                    return candidate, "This category requires another role to remain selected."
            elif maximum == 1:
                candidate = frozenset({toggle_role_id})
            elif len(current) >= maximum:
                return current, "This category is already at its role limit."
            else:
                candidate = current | {toggle_role_id}

        if not category.cardinality.minimum <= len(candidate) <= maximum:
            return candidate, "The selection does not satisfy this category's cardinality."
        return candidate, None

    async def _notice(self, interaction: discord.Interaction[Any], result: RoleTransitionResult) -> None:
        """Run the configured notice hook or the safe built-in hook."""
        if self.notice is None:
            await _send_default_notice(interaction, result)
        else:
            await self.notice(interaction, result)


__all__ = [
    "ANY",
    "AT_LEAST_ONE",
    "AT_MOST_ONE",
    "EXACTLY_ONE",
    "Cardinality",
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
]
