"""Schema-driven field commands over explicitly selected aggregates.

Stores own locking and revision persistence. The command carries the loaded root
through preparation so validation, auditing, and saving use the same instance.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision
from squid.persistence.revisions import require_revision


@dataclass(frozen=True, slots=True)
class Aggregate[UowT: EditUnitOfWork, RootT]:
    target: EditTarget | str
    load: Callable[[UowT, EditCommand], Awaitable[RootT]]
    revision: Callable[[RootT], int]
    flush: Callable[[UowT, RootT, Revision], Awaitable[Revision]]

    async def lock(self, uow: UowT, command: EditCommand) -> RootT:
        if len(command.expected) != 1:
            raise ValidationError
        target = command.expected[0].target
        if (target.kind if isinstance(self.target, str) else target) != self.target:
            raise ValidationError
        root = await self.load(uow, command)
        expected = command.expected[0]
        current = self.revision(root)
        require_revision(expected, current)
        return root


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedFields[RootT, ValuesT: BaseModel](PreparedEdit):
    root: RootT
    previous: ValuesT
    desired: ValuesT


class FieldCommand[UowT: EditUnitOfWork, RootT, ValuesT: BaseModel]:
    """Edit only fields declared by a selected schema, with attribute mapping by default.

    Authorization runs even for a no-op. An optional audit callback participates in
    the same transaction as the write. Override capture/write for domain projections.
    """

    def __init__(
        self,
        aggregate: Aggregate[UowT, RootT],
        values: type[ValuesT],
        authorize: Callable[[UowT, Actor, RootT], Awaitable[None]],
        *,
        audit: Callable[[UowT, Actor, ValuesT, ValuesT], Awaitable[None]] | None = None,
    ) -> None:
        self.aggregate = aggregate
        self.values = values
        self.authorize = authorize
        self.audit = audit

    async def capture(self, uow: UowT, root: RootT, command: EditCommand) -> ValuesT:
        return self.values.model_validate(root, from_attributes=True)

    async def validate(
        self, uow: UowT, actor: Actor, root: RootT, command: EditCommand, before: ValuesT, after: ValuesT
    ) -> ValuesT:
        return after

    async def write(self, uow: UowT, root: RootT, before: ValuesT, after: ValuesT) -> None:
        for name in self.values.model_fields:
            setattr(root, name, getattr(after, name))

    async def prepare(self, uow: UowT, actor: Actor, command: EditCommand) -> PreparedFields[RootT, ValuesT]:
        changes = command.values.unpack()
        if changes.keys() - self.values.model_fields.keys():
            raise ValidationError
        root = await self.aggregate.lock(uow, command)
        await self.authorize(uow, actor, root)
        previous = await self.capture(uow, root, command)
        desired = self.values.model_validate(previous.model_dump(mode="json") | changes)
        desired = await self.validate(uow, actor, root, command, previous, desired)
        return PreparedFields(
            EditValues.of(previous.model_dump(mode="json")),
            EditValues.of(desired.model_dump(mode="json")),
            revisions=(Revision(command.expected[0].target, self.aggregate.revision(root)),),
            root=root,
            previous=previous,
            desired=desired,
        )

    async def apply(
        self, uow: UowT, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedFields)
        await self.write(uow, prepared.root, prepared.previous, prepared.desired)
        if self.audit is not None:
            await self.audit(uow, actor, prepared.previous, prepared.desired)
        return (await self.aggregate.flush(uow, prepared.root, prepared.revisions[0]),)
