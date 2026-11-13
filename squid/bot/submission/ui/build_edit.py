"""The private Discord editor for changing a saved build.

It edits a build through the same form, sections, and pickers as a submission draft (see
`squid.bot.submission.ui.sections`), using the edit view of the build form
(`squid.submissions.application.forms.build_edit_manifest`). Unlike a draft, nothing is saved
until the user asks:

1. Opening the editor turns the build into form answers (`answers_from_build`).
2. Edits change those answers in a `BuildEditWorkspace`; Undo and Redo step through them.
3. Review changes lists only what differs from the saved build, and Apply changes saves one
   build patch (`patch_from_answers`) through the versioned build edit, which checks the
   revision the editor loaded so a concurrent change is reported instead of overwritten.

Restriction and pattern names that match no catalogue entry cannot be shown in a picker. The
editor lists them under their section and lets the user remove them; new names go through tag
proposals instead.
"""

from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from copy import deepcopy
from typing import Any, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_controller import EditController
from squid.bot.edit_history import history_controls
from squid.bot.submission.ui.editor import BuildEditorScreen, EditorSection
from squid.bot.submission.ui.sections import form_sections, problem_text, section_text
from squid.bot.ui import DISCORD_BLUE, DISCORD_YELLOW, tr
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.application.edits import BUILD_EDIT, build_target, patch_values
from squid.builds.domain import UnresolvedTaxonomy
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import JSONValue
from squid.edits.application import EditRunner
from squid.edits.domain import RevisionConflictError
from squid.submissions.application.build_answers import BuildAnswers, answers_from_build, patch_from_answers
from squid.submissions.domain import ChoiceOption, FormField, FormManifest, FormSection, SubmissionOrigin
from squid_i18n import Text, md_tr
from squid_reactivity import TopicHandle
from squid_ui.text import TextLike, md

type OptionLoader = Callable[[str], Awaitable[tuple[ChoiceOption, ...]]]
"""Fetch the current choices of one option source, such as `approved_restrictions`."""

type CardRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]]

_MAX_SELECT_OPTIONS = 25


class BuildEditWorkspace(sl.Reactive):
    """A saved build's answers while they are being edited, shared by every message the editor opens.

    The workspace keeps the answers the build had when it was loaded (`saved`) and the edited
    answers (`staged`). Each change is a step in `history`. Nothing reaches the build until the
    screen saves `patch()`; after a save, `reset` starts again from the saved build.
    """

    staged: Mapping[str, JSONValue] = sl.state({}, opaque=True)
    """Edited answers keyed by field ID; a field the build has no value for holds None or []."""
    unresolved: UnresolvedTaxonomy = sl.state(UnresolvedTaxonomy(), opaque=True)
    """Names on the build that match no catalogue entry and that the user has not removed."""
    notice: sl.TextLike = sl.state("")
    option_sets: Mapping[str, tuple[ChoiceOption, ...]] = sl.state({}, opaque=True)
    revision: int = sl.state(0)
    """Counts changes; a section modal compares it to notice that it was opened before an undo."""
    history: sl.runtime.History = sl.runtime.history(limit=20)

    def __init__(
        self,
        build: Build,
        manifest: FormManifest,
        option_sets: Mapping[str, tuple[ChoiceOption, ...]],
        *,
        load_options: OptionLoader,
    ) -> None:
        """Start from `build`.

        `option_sets` must hold the restriction and pattern catalogues, which decide which of the
        build's names are resolved; other catalogues are fetched by `load_options` when needed.
        """
        self.manifest = manifest
        self.category = build.category.value.lower()
        self._load_options = load_options
        self.option_sets = dict(option_sets)
        self.saved = BuildAnswers({}, UnresolvedTaxonomy())
        self.reset(build)

    def reset(self, build: Build) -> None:
        """Discard edits and their history, and start again from `build` as saved."""
        self.saved = answers_from_build(build, self.option_sets)
        self.staged = dict(self.saved.answers)
        self.unresolved = self.saved.unresolved
        self.notice = ""
        self.history.clear()

    @property
    def origin(self) -> SubmissionOrigin:
        return SubmissionOrigin.DISCORD

    @property
    def answers(self) -> dict[str, JSONValue]:
        return self.manifest.apply_defaults(self.category, self.staged, origin=self.origin)

    @property
    def stored_answers(self) -> Mapping[str, JSONValue]:
        return self.staged

    @property
    def fields(self) -> tuple[FormField, ...]:
        answers = self.answers
        return tuple(
            field for field in self.manifest.fields_for(self.category) if field.is_visible(answers, self.origin)
        )

    @property
    def sections(self) -> tuple[FormSection, ...]:
        return form_sections(self.manifest, self.category, self.fields)

    def section(self, section_id: str) -> FormSection | None:
        return next((section for section in self.sections if section.id == section_id), None)

    @property
    def editable(self) -> bool:
        return True

    def history_controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return history_controls(self.history)

    async def load_options(self, fields: Iterable[FormField]) -> None:
        sources = {field.option_source for field in fields if field.option_source is not None}
        missing = sources - self.option_sets.keys()
        if missing:
            self.option_sets = {**self.option_sets, **{source: await self._load_options(source) for source in missing}}

    async def save_values(self, values: Mapping[str, JSONValue], *, base_revision: int | None = None) -> None:
        """Stage several answers as one undoable step; invalid values stage nothing and set `notice`.

        Must run inside a UI action, which records the step in `history`.
        """
        if base_revision is not None and base_revision != self.revision:
            self.notice = tr(t"build-editor-stale-form = These answers changed while the form was open. Open it again.")
            return
        if (problem := self._problem(values)) is not None:
            self.notice = problem
            return
        self.staged = {**self.staged, **values}
        self.revision += 1
        self.notice = ""
        self.history.record(tr(t"build-edit-history-fields = Edit build fields"))

    def prefill(self, values: Mapping[str, JSONValue]) -> TextLike | None:
        """Stage answers before the editor is shown, outside history; return why they are invalid, if they are."""
        if (problem := self._problem(values)) is not None:
            return problem
        self.staged = {**self.staged, **values}
        return None

    def _problem(self, values: Mapping[str, JSONValue]) -> TextLike | None:
        """Describe the invalid values among `values`, judged with the other staged answers."""
        errors = self.manifest.validate_answers(
            self.category, {**self.staged, **values}, origin=self.origin, require_complete=False
        )
        labels = {field.id: field.label for field in self.manifest.fields_for(self.category)}
        problems = [
            Text.join("", (md(t"**{labels.get(field_id, field_id)}**"), ": ", problem_text(errors[field_id])))
            for field_id in values
            if field_id in errors
        ]
        return Text.join("\n", problems) if problems else None

    def remove_unresolved(self, names: Collection[str]) -> None:
        """Remove names that match no catalogue entry, as one undoable step; must run inside a UI action."""
        current = self.unresolved
        self.unresolved = UnresolvedTaxonomy(
            restrictions=tuple(pair for pair in current.restrictions if pair[1] not in names),
            patterns=tuple(name for name in current.patterns if name not in names),
        )
        self.revision += 1
        self.history.record(tr(t"build-edit-history-unlisted = Remove names not in the catalogue"))

    def changed_fields(self) -> frozenset[str]:
        """IDs of fields whose staged answer differs from the saved one."""
        return frozenset(
            field.id for field in self.fields if self.staged.get(field.id) != self.saved.answers.get(field.id)
        )

    def removed_names(self) -> tuple[str, ...]:
        """Unresolved names the user removed, in the order the build listed them."""
        kept = {*self.unresolved.restriction_names(), *self.unresolved.patterns}
        listed = (*self.saved.unresolved.restriction_names(), *self.saved.unresolved.patterns)
        return tuple(name for name in listed if name not in kept)

    def patch(self) -> BuildEditPatch:
        """The changes to save, in the build patch's terms; empty when nothing differs from the saved build."""
        return patch_from_answers(self.saved, self.staged, self.unresolved, self.option_sets)


class BuildEditScreen(BuildEditorScreen[int, BuildEditWorkspace]):
    """Edit a saved build's sections, review the changes, and save them as one build patch.

    The loaded build follows its topic so the card preview stays current, while staged answers
    keep the revision they were loaded at. Saving checks current authority and that revision; on
    a conflict the user can reload, which discards staged answers. Closing without saving
    cancels the screen; closing after a save completes it with the build ID.
    """

    policy = sd.ResponseSpec(
        timeout=900, live_updates=True, retain_routed_on_timeout=True, session=sd.SessionSpec("build-edit")
    )

    _base_revision: int = sl.state(0)
    confirming: bool = sl.state(default=False)
    saved: bool = sl.state(default=False)
    conflict: bool = sl.state(default=False)
    """The build changed after the staged answers were loaded, so they cannot be saved."""

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        workspace: BuildEditWorkspace,
        *,
        node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None = None,
        authorize: Callable[[], Awaitable[bool]],
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        render_build: CardRenderer,
        refresh_posts: Callable[[int], Awaitable[None]],
        changes: TopicHandle | None = None,
        recovered: bool = False,
    ) -> None:
        if build.id is None:
            message = "only saved builds can be edited"
            raise ValueError(message)
        self._seed: tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None] | None = (build, node)
        self._changes = changes
        self._build_id: int = build.id
        self._base_revision = build.revision
        self.builds = builds
        self.workspace = workspace
        self._authorize = authorize
        self.controller = EditController(edits, actor, self._refresh_saved)
        self._render_build = render_build
        self._refresh_posts = refresh_posts
        self._recovered = recovered

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None]:
        """Load the edited build and keep its preview current with the build topic."""
        if self._changes is not None:
            self._changes.watch()
        seed, self._seed = self._seed, None
        if seed is not None:
            return seed
        latest = await self.builds.get(self._build_id)
        if latest is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return latest, await self._render_build(latest)

    def _current(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None]:
        if self._seed is not None:
            return self._seed
        state = self.projection.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if state.previous is not None:
            return state.previous.value
        message = "this editor has not loaded a build yet"
        raise sl.resources.ResourceNotReadyError(message)

    @property
    def build(self) -> Build:
        return self._current()[0]

    async def may_edit(self) -> bool:
        """Recheck whether the actor may currently edit this build."""
        return await self._authorize()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self.expired:
            return self._expired()
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-ui-views-loading-build = Loading build.")),)
        ws = self.workspace
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        if self._recovered:
            nodes.append(
                sl.status(
                    tr(
                        t"build-editor-reloaded-discarded-changes = Fresh "
                        t"editor loaded. Unsaved changes from the previous editor were discarded."
                    ),
                    tone=sl.Tone.WARNING,
                )
            )
        if self.conflict:
            description: TextLike = tr(
                t"build-editor-concurrent-change = This build "
                t"changed while you were editing. Reload the latest version; your staged changes will "
                t"be discarded."
            )
        elif ws.notice:
            description = Text.join("", ("⚠️ ", ws.notice))
        elif self.confirming:
            description = tr(t"build-editor-review-hint = Review the changed values below, then apply them.")
        else:
            description = tr(
                t"build-editor-staging-hint = Edit each section, then review and save. Changes are not saved yet."
            )
        children: list[Any] = [
            sd.v2.heading(tr(t"bot-submission-ui-views-edit-build = Edit build")),
            sd.v2.text(description),
            sd.v2.separator(),
            *self.section_overview(self._review_sections() if self.confirming else self._sections()),
        ]
        if not self.confirming and (names := (*ws.unresolved.restriction_names(), *ws.unresolved.patterns)):
            children.append(
                sd.v2.select(
                    *(sd.v2.option(name[:100], name) for name in dict.fromkeys(names[:_MAX_SELECT_OPTIONS])),
                    on_select=self._remove_unlisted,
                    key="remove_unlisted",
                    placeholder=tr(t"build-editor-remove-unlisted = Remove names not in the catalogue…"),
                    max_values=min(len(set(names)), _MAX_SELECT_OPTIONS),
                )
            )
        children.extend(
            (
                sd.v2.separator(),
                sl.action_controls(*self._actions(), key="build-edit-actions"),
            )
        )
        nodes.append(sd.v2.panel(*children, accent=DISCORD_YELLOW if self.conflict or ws.notice else DISCORD_BLUE))
        nodes.extend(ws.history_controls())
        if self.controller.refresh_failed or (not ws.patch().attributes() and not ws.history.can_redo):
            nodes.extend(self.controller.controls())
        return tuple(nodes)

    def _expired(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        # Route registration imports the editor through opening.py, so resolve the route here.
        from squid.bot.submission.ui import controls as build_controls

        return (
            sl.status(
                tr(t"build-editor-expired = This editor expired. Unsaved changes were discarded."),
                tone=sl.Tone.WARNING,
            ),
            sl.action_controls(
                sl.routed_action_control(
                    tr(t"build-editor-open-new-editor = Open new editor"),
                    build_controls.recover_build_editor.id(build_id=self._build_id),
                    key="restart",
                ),
                key="build-edit-recovery",
            ),
        )

    def _sections(self) -> list[EditorSection]:
        ws = self.workspace
        sections: list[EditorSection] = []
        for section in ws.sections:
            text = section_text(ws, section)
            field_ids = {field.id for field in section.fields}
            unlisted = (
                *(ws.unresolved.restriction_names() if "restrictions" in field_ids else ()),
                *(ws.unresolved.patterns if "patterns" in field_ids else ()),
            )
            if unlisted:
                names = ", ".join(unlisted)
                text = Text.join("\n", (text, md_tr(t"build-editor-unlisted = Not in the catalogue: {names}")))
            sections.append(EditorSection(section.id, text))
        return sections

    def _review_sections(self) -> list[EditorSection]:
        """Only the changed fields, including cleared ones, and the names the user removed."""
        ws = self.workspace
        changed = ws.changed_fields()
        removed = ws.removed_names()
        sections: list[EditorSection] = []
        for section in ws.sections:
            fields = tuple(field for field in section.fields if field.id in changed)
            field_ids = {field.id for field in section.fields}
            lines: list[TextLike] = []
            if fields:
                lines.append(section_text(ws, FormSection(section.id, section.title, fields), show_empty=True))
            if removed and field_ids & {"restrictions", "patterns"}:
                if not lines:
                    lines.append(md(t"**{section.title}**"))
                names = ", ".join(removed)
                lines.append(md_tr(t"build-editor-removed-unlisted = Removed: {names}"))
                removed = ()
            if lines:
                sections.append(EditorSection(section.id, Text.join("\n", lines)))
        return sections

    def _actions(self) -> list[sl.semantic.ActionControl[sd.Request]]:
        """The last row: the main step first, then Preview, then the ways out."""
        actions: list[sl.semantic.ActionControl[sd.Request]] = []
        if self.confirming:
            actions.extend(
                (
                    sl.action_control(
                        tr(t"bot-submission-ui-views-apply-changes = Apply changes"),
                        self._apply,
                        key="apply",
                        tone=sl.Tone.SUCCESS,
                    ),
                    sl.action_control(tr(t"bot-submission-ui-views-back = Back"), self._unconfirm, key="unconfirm"),
                )
            )
        else:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-ui-views-review-changes = Review changes"),
                    self._review,
                    key="review",
                    tone=sl.Tone.SUCCESS,
                    available=not self.conflict,
                )
            )
        actions.append(sl.action_control(tr(t"draft-editor-preview = Preview"), self._preview, key="preview"))
        if self.conflict:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-ui-views-reload-latest = Reload latest"), self._reload, key="reload"
                )
            )
        actions.append(sl.action_control(tr(t"bot-submission-ui-views-cancel = Cancel"), self._close, key="close"))
        return actions

    @override
    async def _edit_section(self, section_id: str, event: sd.PressEvent) -> None:
        if await self._may_event(event):
            await super()._edit_section(section_id, event)

    async def _remove_unlisted(self, event: sd.SelectionEvent) -> None:
        if await self._may_event(event):
            self.workspace.remove_unresolved(event.values)

    async def _preview(self, event: sd.PressEvent) -> None:
        preview = deepcopy(self.build)
        self.workspace.patch().apply(preview)
        await self.open_child(event, BuildEditPreview(await self._render_build(preview)), on_result=self._child_closed)

    async def _review(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event):
            return
        if not self.workspace.patch().attributes():
            self.workspace.notice = tr(t"bot-submission-ui-views-no-changes-to-review-yet = No changes to review yet.")
            return
        self.confirming = True

    async def _unconfirm(self, event: sl.PressEvent) -> None:
        self.confirming = False

    async def _apply(self, event: sd.PressEvent) -> None:
        if not await self._may_event(event):
            return
        patch = self.workspace.patch()
        if not patch.attributes():
            return
        await event.acknowledge()
        try:
            await self.controller.bind(BUILD_EDIT, build_target(self._build_id), self._base_revision).save(
                patch_values(patch), tr(t"build-edit-history-save = Save build changes")
            )
        except RevisionConflictError:
            self.confirming = False
            self.conflict = True
        else:
            self.saved = True

    async def _refresh_saved(self) -> None:
        """Start again from the saved build after a save, an undo of one, or a reload."""
        current = await self.builds.get(self._build_id)
        if current is None:
            raise LookupError(self._build_id)
        self.workspace.reset(current)
        self.confirming = False
        self.conflict = False
        self._base_revision = current.revision
        node = await self._render_build(current)
        if self._seed is not None:
            self._seed = (current, node)
        else:
            self.projection.replace((current, node))
        await self._refresh_posts(self._build_id)

    async def _close(self, event: sd.PressEvent) -> None:
        if self.saved:
            self.complete(event, self._build_id)
        else:
            self.cancel(event)

    async def _reload(self, event: sl.PressEvent) -> None:
        await self.projection.reload()
        if not await self._may_event(event):
            return
        await event.acknowledge()
        self.controller.forget({build_target(self._build_id).identity})
        await self.controller.refresh()

    async def _may_event(self, event: sl.ActionEvent) -> bool:
        if not await self.may_edit():
            await event.notice(
                tr(
                    t"build-editor-edit-access-required-views = Only the pending build's "
                    t"submitter or a trusted staff member can edit it."
                )
            )
            return False
        return True


class BuildEditPreview(sd.Screen[None]):
    """Show the build card with the staged changes, without saving them."""

    policy = sd.ResponseSpec(audience="personal", timeout=600, session=sd.SessionSpec("build-preview"))

    def __init__(self, node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request]) -> None:
        self.node = node

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            self.node,
            sd.v2.row(sd.v2.button(tr(t"draft-editor-done = Done"), self._done, key="done")),
        )

    async def _done(self, event: sd.PressEvent) -> None:
        self.complete(event, None)
