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

## Keeping screens within Discord limits

Test the crowded states, not just the first render: full result pages, detailed cards,
all permission-dependent actions, every tab, and the messages shown after form submission.
`stage()` checks message limits after presentation and interactions, and raises layout
failures even when the application's error policy reports them successfully.

Choose overflow behavior where you compose the screen:

- Put help or detailed content in a separate view when it competes with a browser for space.
  Preserve the browser state so returning does not repeat the query.
- Use `sl.fallback(primary, compact)` for equivalent authored representations. The planner
  selects the compact form under pressure. Keep necessary information and actions reachable;
  a fallback is a complete replacement, not permission to silently discard content.
- Return `Document(..., key="screen")` for standalone content that can span message pages.
  Navigation consumes budget too. Shared root pages can contain local pagers with independent
  positions. Layouts still using a legacy fitter cannot combine root and local pages.
- Set `ResponseSpec(max_messages=2)` when a screen's parts should all stay visible and no
  single message holds them. The screen is cut between top-level nodes and continued in a
  follow-up message; it stays one message while it fits. Wrap a node in `sl.keep_with_next`
  to keep it in the same message as the node after it, such as a Back button and the
  content it closes. Prefer this to paging for short interactive sessions, and keep the
  default of 1 for durable sessions, which restore only the first message.

Search help demonstrates a structured reference with a compact text alternative. Test that
both representations preserve the content, that the preferred form wins when it fits, and
that exhausting every alternative produces a useful failure. Layout error reports include
screen and action identities plus budget metadata without copying rendered content.

For multiple simultaneously visible pagers, use `sl.region` and let the joint
allocator account for their worst-page costs. Keep screen-wide actions in the
outer region's header or footer; keep item actions inside an unbreakable group
with their item. Legacy keyed
`Document` root pagination retains its existing restriction on local pagers;
a tree of regions supplies the joint alternative.

## Addressable destinations

`squid.bot.routes.router` is the single registry for screen openings and persistent
actions. Both use `@router.route(path)`, receive an `AppRequest`, and return either a
`Screen`, a `Response`, or `None` after handling the request themselves.
Screen classmethods inherit their screen's response policy; standalone actions can
supply `policy=ResponseSpec(...)` on the decorator. Screens can set an instance's
`session_key` to override the key derived from their session policy.

```python
@router.route("/builds/{build_id}")
@classmethod
async def open_detail(cls, request: AppRequest, build_id: int) -> Self: ...
```

Names in `{braces}` are path parameters. Other parameters have defaults and appear
in the query string when changed. A named optional fragment parameter, such as
`@router.route("/account", fragment="tab")`, produces `/account#claims`. Integers
are positive IDs; booleans, UUIDs, string Literals, and optional forms are supported.

The decorated attribute is a typed endpoint handle:

```python
await BuildBrowseScreen.open_detail(request, build_id=12)
address = BuildBrowseScreen.open_detail.address(build_id=12)  # /builds/12
custom_id = BuildBrowseScreen.open_detail.custom_id(build_id=12)  # ui:/builds/12
await router.dispatch(request, router.resolve(address))
```

Direct calls and address dispatch use the same middleware for authorization guards,
tracing, acknowledgement, and delivery. Handlers still check current permissions and
reload their resources; addresses carry no permission grants. `ScreenUnavailable`
produces a localized reply under the endpoint's policy.

Persistent buttons carry `ui:` followed by the canonical address, with a hard
100-character limit checked during construction. The Discord adapter strips the
prefix and invokes the same endpoint. There is no forwarding navigation route or
second screen registry. Session-bound callbacks keep their separate mount namespace.
Old colon-form IDs are not accepted by the application adapter.

Import endpoint modules before installing the adapter, which freezes route identities.
Module reloads replace handlers on stable handles, so retained button references use
the current handler. Reloads cannot add routes or change address parameters.

An item can implement `sl.Focusable[Target]` to expose public locations through
`focus_targets()` and reveal one with `focus(target)`. This capability is independent
of `Renderable` and does not move keyboard focus. The account opener calls `focus(tab)`;
the workspace selects a tab immediately or remembers it until loading. Later renders
preserve the user's selection, and another explicit focus call can change it. Unavailable
targets raise `ValueError`, which the account opener translates into `ScreenUnavailable`.
Focus changes presentation without firing action callbacks; the host delivers the render.
