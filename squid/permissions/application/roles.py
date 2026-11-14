"""Translate stored role definitions into the domain resolver's role specifications."""

from collections.abc import Iterable

from squid.permissions.domain import BUILTIN_ROLES_BY_KEY, RoleSpec
from squid.permissions.models import PermissionRole


def role_specs(roles: Iterable[PermissionRole]) -> dict[str, RoleSpec]:
    """Combine stored role patterns with code-defined built-in patterns.

    Stored patterns extend built-ins, but cannot override their code-defined
    exclusions. Exclusions apply after the union, preserving the built-in limits
    on destructive and bot-wide permissions.
    """
    specs: dict[str, RoleSpec] = {}
    for role in roles:
        includes = list(role.includes)
        excludes = list(role.excludes)
        builtin = BUILTIN_ROLES_BY_KEY.get(role.builtin_key or "")
        if builtin is not None:
            includes = [*builtin.includes, *includes]
            excludes = [*builtin.excludes, *excludes]
        specs[str(role.id)] = RoleSpec(
            key=str(role.id),
            includes=tuple(dict.fromkeys(includes)),
            excludes=tuple(dict.fromkeys(excludes)),
            includes_roles=tuple(str(edge) for edge in role.includes_roles),
        )
    return specs
