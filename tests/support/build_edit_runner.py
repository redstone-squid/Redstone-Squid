"""Small in-memory build editor for transport tests; database tests exercise the handler."""

from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.edits import build_values
from squid.builds.models import Build, DoorBuild
from squid.edits.domain import (
    EditActor,
    EditAttempt,
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    Revision,
    RevisionConflictError,
)
from squid.permissions.domain import Subject


async def build_edit_actor() -> EditActor:
    return EditActor(Subject(account_id=1))


class BuildEditRecorder:
    def __init__(self, build: Build | None = None, *, error: Exception | None = None) -> None:
        self.build = build or DoorBuild(id=1)
        self.error = error
        self.commands: list[EditCommand] = []
        self.actors: list[EditActor] = []

    async def execute(
        self, command: EditCommand, actor: EditActor, *, attempt: EditAttempt | None = None
    ) -> EditResult:
        self.commands.append(command)
        self.actors.append(actor)
        if self.error is not None:
            raise self.error
        expected = command.expected[0]
        if self.build.revision != expected.value:
            raise RevisionConflictError(expected, self.build.revision)
        before = build_values(self.build)
        values = command.values.unpack()
        values.pop("unresolved_taxonomy", None)
        BuildEditPatch.from_attributes(values).apply(self.build)
        after = build_values(self.build)
        if before != after:
            self.build.revision += 1
        revisions = (Revision(expected.target, self.build.revision),)
        receipt = EditReceipt(command, before, after, revisions)
        if attempt is not None:
            attempt.receipt = receipt
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=before != after, final=False
            ),
            receipt,
        )

    async def reconcile(self, command: EditCommand, actor: EditActor, attempt: EditAttempt) -> EditResult | None:
        raise NotImplementedError
