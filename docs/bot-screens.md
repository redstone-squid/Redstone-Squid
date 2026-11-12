# Bot screen conventions

These rules keep the bot's Discord screens consistent. A screen is one interactive message built
with Squid UI, such as `/notifications` or the build browser. Run `just ui-gallery` to see every
screen the tests render, and compare it before and after a layout change.

## Rows of controls

Discord stacks every row of buttons and every select menu on its own line, so each extra row
makes a screen longer and harder to scan.

- Put related controls in one `sl.action_controls`. It holds actions, links, toggles, and forms,
  and keeps them in the order given. A toggle or form outside it takes a whole row by itself.
- End a screen with one row of controls. Put the main action first, then secondary actions such
  as Undo, and the action that leaves the screen, such as Cancel or Back, last.
- Put a note about the main action, such as "Publishing is final", just above that last row.
- Previous and Next keep their left-to-right order, because they mirror the direction they move.

## Hidden and greyed-out controls

- Grey out a control the reader can enable on this screen: Submit before the required fields are
  filled, or an action on selected items before anything is selected. The greyed button tells the
  reader what the screen can do next.
- Hide a control nothing on the screen can enable: actions on an empty list, Undo with nothing to
  undo, or Redo before anything has been undone.
- Keep paging controls in place, greyed out, at either end of a list, so they do not move under
  the reader's pointer. Leave them out when there is only one page.
- Never show a toggle that is greyed out with a made-up state. If it needs a selection to mean
  anything, hide it until something is selected.

## Settings and editors

- Settings screens, such as notifications or server settings, have no Undo or Redo: changing a
  setting back reverses it. They render `EditController.refresh_controls()` so the reader can
  reload after a failed refresh.
- Editors that change several fields before saving, such as the build editor or the poll wizard,
  keep Undo and Redo.
- Show each value once. When a toggle shows a setting's state, do not repeat it in the summary.

## Paging labels

Browsers say Previous and Next. Pass `chronology=sl.sources.Chronology.NEWEST_FIRST` (or
`OLDEST_FIRST`) only for lists sorted by time; they then say Newer and Older, pointing the way each
step really goes. Check the query's sort order before declaring it.
