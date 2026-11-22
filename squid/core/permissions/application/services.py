"""Load a subject's permission sources and resolve the capabilities they hold.

Direct grants attach patterns to an account or Discord role. Permission-role
assignments attach named bundles of patterns instead; these are Squid roles,
separate from Discord roles. The service expands both sources into rules for the
domain resolver. Discord's Manage Server permission also supplies the built-in
guild-admin role, through what we call the guild-admin bridge.

Assembled rules can be cached across checks. Resolution still evaluates expiry,
scope, and precedence for each requested capability.
"""

from contextlib import nullcontext

from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import InvalidStateError
from squid.core.permissions.application.cache import SubjectRuleCache, cache_key
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.application.roles import PermissionRuleSnapshot
from squid.core.permissions.domain import (
    CATALOGUE,
    Catalogue,
    CheckMode,
    PermissionCheck,
    PermissionNode,
    Rule,
    resolve,
)
from squid.core.permissions.errors import require_check
from squid.core.unit_of_work import UnitOfWorkFactory


class PermissionService:
    """Answer permission questions for one subject at a time."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[PermissionUnitOfWork],
        catalogue: Catalogue = CATALOGUE,
        cache: SubjectRuleCache | None = None,
    ):
        self._uow_factory = uow_factory
        self._catalogue = catalogue
        self._cache = cache

    @property
    def catalogue(self) -> Catalogue:
        """The node catalogue this service resolves against."""
        return self._catalogue

    async def check(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> PermissionCheck:
        """Evaluate requested nodes once, retaining both grants and denial reasons.

        Supplying `uow` reads its current rules without consulting or populating
        the cache, and leaves its transaction lifecycle to the caller. Otherwise
        the service uses its normal cached lookup.
        """
        rules = await self._rules_for(subject, uow=uow)
        now = Instant.now()
        return PermissionCheck(
            tuple(resolve(node, subject, rules, catalogue=self._catalogue, now=now) for node in nodes), mode=mode
        )

    async def require(
        self,
        subject: Actor,
        *nodes: PermissionNode | str,
        uow: PermissionUnitOfWork | None = None,
        mode: CheckMode = "all",
    ) -> None:
        """Enforce the same check and raise the shared explanatory ACL error on denial."""
        require_check(await self.check(subject, *nodes, uow=uow, mode=mode))

    async def _rules_for(self, subject: Actor, *, uow: PermissionUnitOfWork | None) -> tuple[Rule, ...]:
        if subject.is_bot_owner:
            return ()
        cache = self._cache if uow is None else None
        key = cache_key(subject)
        if cache is not None and (cached := cache.get(key)) is not None:
            return cached
        async with self._uow_factory() if uow is None else nullcontext(uow) as scope:
            records = await scope.permissions.load_for_subject(subject)
        if records.subject != subject:
            message = "Permission inputs belong to a different identity or context"
            raise InvalidStateError(message)
        rules = PermissionRuleSnapshot(records, catalogue=self._catalogue).rules
        if cache is not None:
            cache.put(key, rules, epoch=records.epoch)
        return rules
