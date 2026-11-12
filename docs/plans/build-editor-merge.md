# One build editor for drafts and saved builds

> **Status:** proposed, not started. Agreed in direction on 2026-11-12: edit saved builds through the
> submission form manifest. The decisions at the end still need an answer before phase 2.

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
answers back into a `BuildEditPatch`. The HTTP patch contract (`edit_schema.py`) does not change.

### Manifest

Fields say which kind of form shows them, alongside the existing origin rule (`FormField.is_visible`). A new
`FormPurpose` (`submit`, `edit`) is separate from `SubmissionOrigin`, which names the client, not the task.

- **Submission only:** schematic visibility, licence and rights attestation, sponsor attribution, source version,
  pattern and restriction proposals, AI-generated, extender timings. The build stores these only as provenance, or
  `BuildEditPatch` cannot change them.
- **Edit only:** image, video, and world download links, and server address, coordinates, and command. The
  manifest has no fields for them today; they get one "Links and location" section.
- **Both:** every other field. The category is shown but cannot change, because door and extender data are
  structural.

Where the manifest and the patch disagree on limits (description 4000 against 10000 characters, creators 20
against 100, restrictions 40 against 100 per list, patterns 20 against 50, completion 200 against 100), the edit
form uses the stored limits so that opening a build never truncates it. Submission limits stay as they are.

### Mapping saved builds to answers and back

A new application module, `squid/submissions/application/build_answers.py`, owns both directions so they can be
tested as a pair.

- `answers_from_build(build, manifest, options)` produces the answers and the names it could not resolve. Patterns
  and restrictions resolve to approved tag keys by the casefolded match `prefill._known` already uses.
- `patch_from_answers(build, before, after, definitions)` compares only fields visible in the edit form and builds
  a patch from the changed ones. Restriction keys go back into their four lists through the same lookup
  `build_target._restriction_values` uses.

Known traps, each needing a test:

- **Extender orientation** is stored as "Upward" or "Downward" but the editor accepts only Horizontal, Up, and
  Down. Map both spellings, and send nothing when the value did not change.
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
3. `DraftEditorScreen` and `BuildEditScreen` become one screen with a mode-specific last row, following
   [the screen conventions](../bot-screens.md):
   - draft: Submit, Preview, Undo, Redo;
   - saved build: Review changes (then Apply changes), Preview, Undo, Redo, Cancel.
4. Delete `EDIT_FIELDS`, `EDIT_SECTIONS`, `squid/bot/submission/ui/fields.py`, and the shared base in `editor.py`.

## Phases

Each phase lands as its own commits with tests.

1. Manifest purposes, edit-only fields, and edit limits. No user-visible change for submissions.
2. `build_answers.py` with round-trip tests over door, extender, and other builds, including every trap above.
3. `AnswerWorkspace`, with `DraftWorkspace` moved onto it unchanged. Draft editor tests must pass untouched.
4. `BuildEditWorkspace` and the merged screen. Port `test_components_v2_ui.py` and `test_edit_fields.py`; compare
   both modes in `just ui-gallery` before and after.
5. Delete the old editor code.

## Decisions needed

1. **Restrictions and patterns become catalogue pickers when editing**, as they are for drafts. Names on a build
   that match no approved tag stay on it and are listed, but can only be removed, not retyped. Recommended: yes;
   new names go through tag proposals.
2. **Showcase tags, versions, and extender timings** cannot be edited today. Recommended: keep them out of the edit
   form for now; each needs a patch field first.
3. **AI-generated** is stored but `BuildEditPatch` cannot change it through the shared contract. Recommended: keep
   it out of the edit form.
