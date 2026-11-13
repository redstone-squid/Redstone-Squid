# One build editor for drafts and saved builds

> **Status:** agreed 2026-11-12, done 2026-11-13; the saved-build editor is `squid/bot/submission/ui/build_edit.py`.
> Saved builds are edited through the submission form manifest. Restrictions and patterns are catalogue pickers;
> showcase tags, versions, and extender timings are editable; AI-generated stays read-only.

## Problem

Discord has two editors for the same build data.

- **The draft editor** (`DraftEditorScreen`, `squid/bot/submission/ui/drafts.py`) fills in a new submission. Its
  sections, fields, labels, and pickers come from the submission form manifest (`squid/submissions/application/
  forms.py`). Each change is saved to the draft immediately.
- **The build edit screen** (`BuildEditScreen`, `squid/bot/submission/ui/views.py`) changes a saved build. Its
  sections and fields are a hard-coded list (`EDIT_FIELDS`, `EDIT_SECTIONS`), and changes stay local until the user
  reviews them and saves one `BuildEditPatch`.

They share only the overview layout (`BuildEditorScreen`). As a result the same build reads differently in each:
"Size and version" against "Build size and versions", "Timings" against "Timing", restrictions as catalogue
pickers against four free-text lists, and empty values as "Optional. Nothing added yet." against a bare
`Build name:`. Every new build attribute has to be added to both.

## Design

The manifest becomes the only description of build fields in Discord. A saved build is edited by turning it into
manifest answers, editing those answers with the draft editor's sections and pickers, and turning the changed
answers back into a `BuildEditPatch`. The HTTP patch contract (`edit_schema.py`) gains the fields the editor needs
that it does not have yet: showcase tags, versions, and extender timings.

### One build form

The submission manifest (`squid/submissions/application/forms.py`) becomes the description of every build field
for both tasks. Each field lists the tasks that show it (`FormPurpose`: submit, edit), and `EditRules` relax its
required flag or limits when editing. `FormManifest.for_purpose` returns the view for one task, and callers only
ever see a view: `build_submission_manifest` for drafts and the HTTP API, `build_edit_manifest` (or
`SubmissionFormService.edit_manifest`) for the Discord editor.

- **Submit only:** schematic visibility, licence and rights attestation, sponsor attribution, AI-generated, tag
  proposals, and the single source version.
- **Edit only:** the build's list of versions, and a "Links and location" section for media links and server
  details.
- **Edit rules:** the limits of `BuildPatch`, so opening a build saved under larger limits never truncates it.
  Creators are optional when editing, because older builds can have none.
- The category is shown but cannot change, because door and extender data are structural.

Checked-in revisions stay immutable for submissions: edit-only fields and edit rules never appear in the submit
view, which is what drafts pin and the API publishes.

### Mapping saved builds to answers and back

A new application module, `squid/submissions/application/build_answers.py`, owns both directions so they can be
tested as a pair.

- `answers_from_build(build, options)` produces the answers and the names it could not resolve. Patterns and
  restrictions resolve to approved tag keys by `match_choice`, the casefolded match prefill uses.
- `patch_from_answers(before, after, unresolved, options)` builds a patch from the answers that changed. Resolved
  restriction names all travel in one list, because the build edit command re-files them by tag type.

Known traps, each needing a test:

- **Extender orientation** was stored as "Upward" by submissions but "Up" by the editor. Phase 0 made the full
  spelling canonical; still send nothing when the value did not change.
- **Unresolved tag names** (names on the build with no approved tag) are not in the picker. Keep them unless the
  user removes them, and show them in the section summary. Do not add them back into the patch:
  `BuildEditPatch.apply` clears them for every list it replaces, and the build edit handler in
  `squid/builds/application/edits.py` then restores the retained ones.
- **Defaults:** `FormManifest.apply_defaults` fills missing answers, for example `ai_generated = False`. Diff the
  answers taken from the build, not defaulted ones, so an untouched field never reaches the patch.
- **Dimensions** must be sent as a whole tuple. Clearing a door width or height would reset it to 1 or 2, so the
  edit form treats them as required.

### Discord

1. Extract an `AnswerWorkspace` protocol from `DraftWorkspace` with what the section editors use: `answers`,
   `fields`, `sections`, `section`, `option_sets`, `load_options`, `save_values`, `notice`, and the revision a modal
   saves against.
2. `DraftWorkspace` keeps its behaviour. A new `BuildEditWorkspace` stages answers locally with an undo history,
   and its review step shows only changed fields before one save through `EditController`.
3. `DraftEditorScreen` and `BuildEditScreen` stay two thin screens over the shared parts: the section overview
   and section editing in `BuildEditorScreen` (`editor.py`) and `sections.py`. Each owns only its workspace and
   its last row, following [the screen conventions](../../bot-screens.md):
   - draft: Submit, Preview, Undo, Redo;
   - saved build: Review changes (then Apply changes), Preview, Undo, Redo, Cancel.
4. Delete `EDIT_FIELDS`, `EDIT_SECTIONS`, and `squid/bot/submission/ui/fields.py`.

### Patch fields for showcase tags, versions, and extender timings

- **Showcase tags** are tag assignments by key. `apply_build_taxonomy` keeps them today
  (`squid/builds/application/taxonomy.py`); the patch replaces the showcase set and leaves restriction and pattern
  tags to their own fields.
- **Versions** are the build's `build_versions` rows. The patch replaces the set, using the same version lookup as
  submission (`source_version` falls back to the newest Java version only for new submissions).
- **Extender timings** are columns on the extender (`extension_time`, `retraction_time`), as doors have, filled
  from submission provenance by a migration. Finalization writes them and records fall back to them when an
  extender has no measured variant, as they do for doors. (The first idea, writing a `default` row into
  `extender_timing_variants`, would have made the builds module write records' table and reproduced the door
  staleness problem below.)
- **Extender directions** are stored as Horizontal, Upward, or Downward. The editor used to store "Up" and "Down",
  which split records into two categories per direction; a migration rewrote them.

Door timings have a related problem, outside this plan: a migration copied each door's timings into
`door_timing_variants` once, and records prefer that row over the door's own columns, so later edits to an older
door's timings never reach records. Track it separately.

## Phases

Each phase lands as its own commits with tests.

0. Patch and HTTP fields for showcase tags, versions, and extender timings, with finalization writing the extender
   timings. Regenerate the OpenAPI contract.
1. Purposes and edit rules in the build form. No change to the submit view.
2. `build_answers.py` with round-trip tests over door, extender, and other builds, including every trap above.
3. `AnswerWorkspace`, with `DraftWorkspace` moved onto it unchanged. Draft editor tests must pass untouched.
4. `BuildEditWorkspace` and the saved-build screen on the shared parts. Port `test_components_v2_ui.py` and `test_edit_fields.py`; compare
   both modes in `just ui-gallery` before and after.
5. Delete the old editor code.

## Decisions

1. Restrictions and patterns become catalogue pickers when editing. Names on a build that match no approved tag
   stay on it and are listed, but can only be removed, not retyped; new names go through tag proposals.
2. Showcase tags, versions, and extender timings become editable (phase 0).
3. AI-generated stays out of the edit form.
