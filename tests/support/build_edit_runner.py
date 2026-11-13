"""Small in-memory build editor for transport tests; database tests exercise the handler."""

from squid.bot.submission.ui.build_edit import BuildEditWorkspace
from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.edits import build_values
from squid.builds.models import Build, DoorBuild
from squid.core.actors import Actor
from squid.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    Revision,
    require_revision,
)
from squid.submissions.application.forms import build_edit_manifest
from squid.submissions.domain import ChoiceOption


async def build_edit_actor() -> Actor:
    return Actor(account_id=1)


class BuildEditRecorder:
    def __init__(self, build: Build | None = None, *, error: Exception | None = None) -> None:
        self.build = build or DoorBuild(id=1)
        self.error = error
        self.commands: list[EditCommand] = []
        self.actors: list[Actor] = []

    async def execute(self, command: EditCommand, actor: Actor) -> EditResult:
        self.commands.append(command)
        self.actors.append(actor)
        if self.error is not None:
            raise self.error
        expected = command.expected[0]
        require_revision(expected, self.build.revision)
        before = build_values(self.build)
        values = command.values.unpack()
        values.pop("unresolved_taxonomy", None)
        BuildEditPatch.from_attributes(values).apply(self.build)
        after = build_values(self.build)
        if before != after:
            self.build.revision += 1
        revisions = (Revision(expected.target, self.build.revision),)
        receipt = EditReceipt(command, before, after, revisions)
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=before != after, final=False
            ),
            receipt,
        )


EDIT_OPTIONS: dict[str, tuple[ChoiceOption, ...]] = {
    "approved_restrictions": (ChoiceOption("observerless", "Observerless"), ChoiceOption("seamless", "Seamless")),
    "approved_patterns": (ChoiceOption("regular", "Regular"), ChoiceOption("full_lamp", "Full Lamp")),
    "approved_showcase_tags": (ChoiceOption("compact", "Compact"),),
    "approved_source_versions": (ChoiceOption("Java 1.20", "Java 1.20"), ChoiceOption("Java 1.21", "Java 1.21")),
}
"""Small catalogues for saved-build editors in tests."""


async def _no_more_options(source: str) -> tuple[ChoiceOption, ...]:
    raise AssertionError(f"the test did not expect {source} to be loaded")


def edit_workspace(build: Build) -> BuildEditWorkspace:
    """A saved-build workspace over the checked-in edit form and `EDIT_OPTIONS`."""
    return BuildEditWorkspace(build, build_edit_manifest(), EDIT_OPTIONS, load_options=_no_more_options)
