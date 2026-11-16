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
with their item. The [joint region API](squid-ui-quickstart.md#joint-page-regions)
explains identity, page ownership, and allocation diagnostics. Legacy keyed
`Document` root pagination retains its existing restriction on local pagers;
a tree of regions supplies the joint alternative.

## Addressable destinations

Screen and persistent-control routes share `squid_ui.routing.RoutePattern` and
`RouteTable` for compilation, matching, reverse generation, and overlap rejection.
`AddressRoute` adds typed query parameters and fragments to that engine. The screen
adapter owns discovery and delivery; the Discord control adapter owns acknowledgement,
handler reloads, and retired-control responses. `sl.routing.Route` retains the colon
syntax and control ID length limit. Existing imports from `squid_ui.interactions.routing`
remain supported.

The bot's `ScreenRouter` opens a route by building a fresh screen with the opening
request's identity and services. `/open address` is an unadvertised entrypoint; screens
have no address-sharing controls. Supported addresses are `/builds`, `/builds/{build_id}`,
`/drafts`, `/drafts?inbox=true`, `/drafts/{draft_id}`, `/account`, and `/account#claims`
(or another existing account tab). Account and draft-list addresses refer to the opener.

Declare a route by decorating a classmethod on the screen with `@screen_route(path)`,
above `@classmethod`:

```python
@screen_route("/builds/{build_id}")
@classmethod
async def open_detail(cls, request: NavigationRequest, build_id: int) -> ScreenOpening: ...
```

Names in `{braces}` are path parameters. Every other parameter is a query parameter and
needs a default, which `url_for` leaves out of the address. Annotations choose the parsing:
`int` (positive), `bool`, `UUID`, string `Literal`s, and an optional form of these.
`@screen_route("/account", fragment="tab")` instead encodes the optional `tab` parameter
as `/account#claims`; it must default to `None`. The opening method receives the typed
value just as it would a query parameter. A class
may declare several routes, and a subclass does not inherit them. List each screen once in
`navigation.assembly.build_screen_router`; `register` fails for a screen with no routes.

The method loads current data, checks access, and returns `ScreenOpening` with a fresh
screen and an optional session key. It returns `None` when an existing consent flow has
handled the interaction, and raises `ScreenUnavailable(message)` when there is nothing to
show. It does not choose an audience: delivery and deferral both follow the screen's own
`policy`, and the router sends the unavailable message under that audience. `open` also
delivers through the request's existing scope, and the screen's session policy decides
whether an older presentation is replaced.

Refer to a route by its attribute, as in `router.open(request, BuildBrowseScreen.open_detail,
build_id=12)` or `router.url_for(...)`. Arguments are type-checked against the method and
checked again for addressability. Code that cannot import a screen class, such as a legacy
control in a module the screen imports, can use `router.open_address(request, "/drafts/...")`.

Persistent destination buttons use the existing Discord control router and delegate to
`request.client.screen_router`. Keep these component IDs separate from screen addresses:
old buttons remain compatibility entrypoints, while addresses describe destinations.
Neither carries permission grants, callbacks, unsaved form state, or navigation history.

An item can implement `sl.Focusable[Target]` to expose public locations through
`focus_targets()` and reveal one with `focus(target)`. This capability is independent
of `Renderable` and does not move keyboard focus. The account opener calls `focus(tab)`;
the workspace selects a tab immediately or remembers it until loading. Later renders
preserve the user's selection, and another explicit focus call can change it. Unavailable
targets raise `ValueError`, which the account opener translates into `ScreenUnavailable`.
Focus changes presentation without firing action callbacks; the host delivers the render.
