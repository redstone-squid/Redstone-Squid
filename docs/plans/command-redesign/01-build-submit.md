# Phase 1: one `/build submit`

> **Status.** Delivered 2026-08-17, together with this plan.

## Problem

Submission was split across two commands whose flaws were mirror images:

- `/build submit` (app-only): four generic attachment options came *first*, and every typed
  field lived in modals inside the `BuildSubmissionForm` workspace. Discord modals cannot
  autocomplete, so pattern, restrictions, versions, and creators — the fields with curated
  taxonomies and suggestion sources behind them — were blind free text. The guided path was
  the path with the worst data entry.
- `/build submit-full` (hybrid): all ~17 fields as flag options with autocomplete, near the
  25-option cap, no preview-before-submit, and its prefix branch just raised
  `NotImplementedError` — it was slash-only in practice, so the "hybrid" bought nothing.

The dogfooding verdict: `submit-full` got used *because* it had suggestions, despite being the
worse experience otherwise.

## Design

One app command, `/build submit`, that is both the quick path and the guided path:

- **Typed fields become real slash options, ordered by importance, all optional.** `door_size`,
  `door_type`, `pattern`, `build_size`, `versions`, `restrictions`, `creators`, `notes` — with
  the same suggestion sources `submit-full` had (`approved_patterns`,
  `approved_source_versions`, `approved_restrictions`, `creators`). Skipping everything still
  opens the blank guided workspace, so `/build submit` alone keeps working.
- **Attachments move to the end** (`first_attachment` … `fourth_attachment`), still classified
  automatically into image/video/schematic, still schematic-analysed.
- **The workspace stays.** Whatever the options provided pre-fills the draft; the
  `BuildSubmissionForm` opens showing it, catches whatever is missing, previews, and submits.
  With everything typed up front it is a one-click confirm; with nothing typed it is the same
  guided flow as before. The modal remains only as the *editing* surface, where losing
  autocomplete is acceptable because the values were enterable as options.
- **`submit-full` is deleted**, along with its `SubmitDoorFlags` converter class. Fields it had
  that the new options do not (locationality, directionality, opening/closing times, creation
  date, explicit URL lists) stay reachable through the workspace's details modal and
  `/build edit`; they did not earn a slot in the primary form. `BuildService.submit_door`
  stays — the REST API is its remaining caller.

### Option order (the tab order is the form)

`door_size, door_type, pattern, build_size, versions, restrictions, creators, notes,`
`first_attachment, second_attachment, third_attachment, fourth_attachment` — 12 options,
comfortably under the 25 cap.

### Semantics carried over unchanged

- Attachment classification, Catbox mirroring, schematic ingest/duplicate/dimension-mismatch
  evidence.
- Schematic-measured dimensions pre-fill the draft **only when the user did not declare a build
  size** — a declared value always wins (schematic exports are legitimately cropped smaller
  than the measured build).
- Restrictions are classified through `BuildService.classify_restrictions`, same as the details
  modal.
- Dimension parse errors (`door_size`, `build_size`) answer immediately with the standard error
  layout instead of opening a workspace holding bad data.

## Taxonomy edits

- `build submit-full` removed from `UNGATED_COMMANDS` and `EXPECTED_PREFIX_COMMAND_TREE` in
  `tests/unit/bot/test_command_taxonomy.py` (prefix submission was already nonfunctional).
- New pin: `test_guided_submit_puts_attachments_last` asserts the option order — typed fields
  first, the four attachment options as the tail — so the original complaint cannot regress
  silently.
- The autocomplete wiring test picks the new option sources up automatically.

## Not in this phase

- Variadic attachments (Discord has no list-valued option; four slots stay).
- Autocomplete inside modals (Discord does not offer it; the design routes around it instead).
- Non-door categories in the guided flow — the workspace still fixes
  `BuildCategory.DOOR`; widening that is its own future piece.
